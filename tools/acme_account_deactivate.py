#!/usr/bin/env python3
import sys, argparse, subprocess, json, base64, binascii, re, copy, logging, requests

LOGGER = logging.getLogger("acme_account_deactivate")
LOGGER.addHandler(logging.StreamHandler())

def account_deactivate(accountkeypath, acme_directory, log=LOGGER):
    # helper function base64 encode as defined in acme spec
    def _b64(b):
        return base64.urlsafe_b64encode(b).decode("utf8").rstrip("=")

    # helper function to run openssl command
    def _openssl(command, options, communicate=None):
        openssl = subprocess.Popen(["openssl", command] + options,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = openssl.communicate(communicate)
        if openssl.returncode != 0:
            raise IOError("OpenSSL Error: {0}".format(err))
        return out

    # helper function to send signed requests
    def _send_signed_request(url, payload):
        nonlocal jws_nonce
        payload64 = _b64(json.dumps(payload).encode("utf8"))
        protected = copy.deepcopy(jws_header)
        protected["nonce"] = jws_nonce or requests.get(acme_config["newNonce"]).headers['Replay-Nonce']
        protected["url"] = url
        if url == acme_config["newAccount"]:
            del protected["kid"]
        else:
            del protected["jwk"]
        protected64 = _b64(json.dumps(protected).encode("utf8"))
        signature = _openssl("dgst", ["-sha256", "-sign", accountkeypath],
                             "{0}.{1}".format(protected64, payload64).encode("utf8"))
        jws = {
            "protected": protected64, "payload": payload64, "signature": _b64(signature)
        }
        try:
            resp = requests.post(url, json=jws, headers=joseheaders)
        except requests.exceptions.RequestException as error:
            resp = error.response
        finally:
            jws_nonce = resp.headers['Replay-Nonce']
            if resp.text != '':
                return resp.status_code, resp.json(), resp.headers
            else:
                return resp.status_code, json.dumps({}), resp.headers

    # main code
    adtheaders = {'User-Agent': 'acme-dns-tiny/2.0'}
    joseheaders = copy.deepcopy(adtheaders)
    joseheaders['Content-Type'] = 'application/jose+json'

    log.info("Fetch informations from the ACME directory.")
    directory = requests.get(acme_directory, headers=adtheaders)
    acme_config = directory.json()

    log.info("Parsing account key.")
    accountkey = _openssl("rsa", ["-in", accountkeypath, "-noout", "-text"])
    pub_hex, pub_exp = re.search(
        r"modulus:\r?\n\s+00:([a-f0-9\:\s]+?)\r?\npublicExponent: ([0-9]+)",
        accountkey.decode("utf8"), re.MULTILINE | re.DOTALL).groups()
    pub_exp = "{0:x}".format(int(pub_exp))
    pub_exp = "0{0}".format(pub_exp) if len(pub_exp) % 2 else pub_exp
    jws_header = {
        "alg": "RS256",
        "jwk": {
            "e": _b64(binascii.unhexlify(pub_exp.encode("utf-8"))),
            "kty": "RSA",
            "n": _b64(binascii.unhexlify(re.sub(r"(\s|:)", "", pub_hex).encode("utf-8"))),
        },
        "kid": None,
    }
    jws_nonce = None
    
    log.info("Ask CA provider account url.")
    account_request = {}
    account_request["onlyReturnExisting"] = True

    code, result, headers = _send_signed_request(acme_config["newAccount"], account_request)
    if code == 200:
        jws_header["kid"] = headers['Location']
    else:
        raise ValueError("Error looking or account URL: {0} {1}".format(code, result))

    log.info("Deactivating account...")
    code, result, headers = _send_signed_request(jws_header["kid"], {"status": "deactivated"})

    if code == 200:
        log.info("Account key deactivated !")
    else:
        raise ValueError("Error while deactivating the account key: {0} {1}".format(code, result))

def main(argv):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Tiny ACME client to deactivate ACME account",
        epilog="""This script permanently *deactivates* an ACME account.

You should revoke your certificates *before* using this script,
as the server won't accept any further request with this account.

It will need to access the ACME private account key, so PLEASE READ THROUGH IT!
It's around 150 lines, so it won't take long.

Example: deactivate account.key from staging Let's Encrypt:
  python3 acme_account_deactivate.py --account-key account.key --acme-directory https://acme-staging-v02.api.letsencrypt.org/directory"""
    )
    parser.add_argument("--account-key", required=True, help="path to the private account key to deactivate")
    parser.add_argument("--acme-directory", required=True, help="ACME directory URL of the ACME server where to remove the key")
    parser.add_argument("--quiet", action="store_const",
                        const=logging.ERROR,
                        help="suppress output except for errors")
    args = parser.parse_args(argv)

    LOGGER.setLevel(args.quiet or logging.INFO)
    account_deactivate(args.account_key, args.acme_directory, log=LOGGER)

if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])