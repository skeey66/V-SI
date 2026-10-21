---
id: bandit-B105
title: bandit B105 hardcoded_password_string
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B105
---
**B105: Test for use of hard-coded password strings**

The use of hard-coded passwords increases the possibility of password
guessing tremendously. This plugin test looks for all string literals and
checks the following conditions:

- assigned to a variable that looks like a password
- assigned to a dict key that looks like a password
- assigned to a class attribute that looks like a password
- used in a comparison with a variable that looks like a password

Variables are considered to look like a password if they have match any one
of:

- "password"
- "pass"
- "passwd"
- "pwd"
- "secret"
- "token"
- "secrete"

Note: this can be noisy and may generate false positives.

**Config Options:**

None

:Example:

```

    >> Issue: Possible hardcoded password '(root)'
       Severity: Low   Confidence: Low
       CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
       Location: ./examples/hardcoded-passwords.py:5
    4 def someFunction2(password):
    5     if password == "root":
    6         print("OK, logged in")

    - https://www.owasp.org/index.php/Use_of_hard-coded_password
    - https://cwe.mitre.org/data/definitions/259.html

    CWE information added
