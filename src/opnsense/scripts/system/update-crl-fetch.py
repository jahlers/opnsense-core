#!/usr/local/bin/python3
"""
    Copyright (c) 2023-2024 Ad Schellevis <ad@opnsense.org>
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
     notice, this list of conditions and the following disclaimer in the
     documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.
"""

import argparse
import glob
import ipaddress
import sys
import os
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.ssl_ import create_urllib3_context
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.extensions import CRLDistributionPoints

class CustomAdapter(HTTPAdapter):
    CIPHERS = 'ECDSA+SHA256:ECDSA+SHA384:ECDSA+SHA512:ed25519:ed448:rsa_pss_pss_sha256:rsa_pss_pss_sha384:' +\
                'rsa_pss_pss_sha512:rsa_pss_rsae_sha256:rsa_pss_rsae_sha384:rsa_pss_rsae_sha512'
    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = create_urllib3_context(ciphers=self.CIPHERS)
        kwargs['ssl_context'].load_verify_locations('/etc/ssl/cert.pem')
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs['ssl_context'] = create_urllib3_context(ciphers=self.CIPHERS)
        kwargs['ssl_context'].load_verify_locations('/etc/ssl/cert.pem')
        return super().proxy_manager_for(*args, **kwargs)


def fetch_certs(domains):
    result = []
    for domain in domains:
        try:
            ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            print('[!!] refusing to fetch from ip address %s' % domain, file=sys.stderr)
            continue
        url = 'https://%s' % domain
        try:
            print('# [i] fetch certificate for %s' % url)
            s = requests.Session()
            s.mount(url, CustomAdapter())
            with s.get(url, timeout=30, stream=True) as response:
                # XXX: in python > 3.13, replace with sock.get_verified_chain()
                for cert in response.raw.connection.sock._sslobj.get_verified_chain():
                    result.append(cert.public_bytes(1).encode()) # _ssl.ENCODING_PEM
        except Exception as e:
            # XXX: probably too broad, but better make sure
            print("[!!] Chain fetch failed for %s (%s)" % (url, e), file=sys.stderr)


    return result

def main(domains, target_filename):
    # fetch the crl's known in our trust store
    crl_bundle = []
    for filename in glob.glob('/etc/ssl/certs/*.r[0-9]'):
        if os.path.isfile(filename):
            with open(filename, 'r') as f_in:
                crl_bundle.append(f_in.read().strip())
    # add the ones being supplied via the domain distribution points
    for pem in fetch_certs(domains):
        try:
            dp_uri = None
            cert = x509.load_pem_x509_certificate(pem)
            for ext in cert.extensions:
                if type(ext.value) is CRLDistributionPoints:
                    for Distributionpoint in ext.value:
                        dp_uri = Distributionpoint.full_name[0].value
                        print("# [i] fetch CRL from %s" % dp_uri)
                        # XXX: only support http for now
                        s = requests.Session()
                        s.mount(dp_uri, CustomAdapter())
                        response = s.get(dp_uri)
                        if 200 <= response.status_code <= 299:
                            crl = x509.load_der_x509_crl(response.content)
                            crl_bundle.append(crl.public_bytes(serialization.Encoding.PEM).decode().strip())
        except ValueError:
            print("[!!] Error processing pem file (%s)" % cert.issuer if cert else '' , file=sys.stderr)
        except Exception as e:
            if dp_uri:
                print("[!!] CRL fetch failed for %s (%s)" % (dp_uri, e), file=sys.stderr)
            else:
                print("[!!] CRL fetch issue (%s) (%s)" % (cert.issuer if cert else '', e) , file=sys.stderr)

    # flush out bundle
    with open(target_filename, 'w') as f_out:
        f_out.write("\n".join(crl_bundle) + "\n")

parser = argparse.ArgumentParser()
parser.add_argument("-t", help="target filename", type=str, default="/dev/stdout")
parser.add_argument('domains', metavar='N', type=str, nargs='*', help='list of domains to merge')
args = parser.parse_args()
main(args.domains, args.t)
