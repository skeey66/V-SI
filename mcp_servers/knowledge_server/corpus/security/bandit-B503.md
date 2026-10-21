---
id: bandit-B503
title: bandit B503 ssl_with_bad_defaults
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B503
---
**B503: Test for SSL use with bad defaults specified**

This plugin is part of a family of tests that detect the use of known bad
versions of SSL/TLS, please see `../plugins/ssl_with_bad_version` for
a complete discussion. Specifically, this plugin test scans for Python
methods with default parameter values that specify the use of broken
SSL/TLS protocol versions. Currently, detection supports methods using
Python's native SSL/TLS support and the pyOpenSSL module. A MEDIUM severity
warning will be reported whenever known broken protocol versions are
detected.

**Config Options:**

This test shares the configuration provided for the standard
`../plugins/ssl_with_bad_version` test, please refer to its
documentation.

:Example:

```

    >> Issue: Function definition identified with insecure SSL/TLS protocol
    version by default, possible security issue.
       Severity: Medium   Confidence: Medium
       CWE: CWE-327 (https://cwe.mitre.org/data/definitions/327.html)
       Location: ./examples/ssl-insecure-version.py:28
    27
    28  def open_ssl_socket(version=SSL.SSLv2_METHOD):
    29      pass

 - `ssl_with_bad_version`
 - `ssl_with_no_version`
 - https://heartbleed.com/
 - https://en.wikipedia.org/wiki/POODLE
 - https://security.openstack.org/guidelines/dg_move-data-securely.html

    CWE information added

    Added TLS 1.1
