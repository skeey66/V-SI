---
id: bandit-B311
title: bandit B311 random
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B311, CWE-330
---
Standard pseudo-random generators are not suitable for security/cryptographic purposes.

대상 호출: random.Random, random.random, random.randrange, random.randint, random.choice, random.choices, random.uniform, random.triangular, random.randbytes, random.sample, random.randrange, random.getrandbits
심각도: LOW · CWE-330

B311: random
------------

Standard pseudo-random generators are not suitable for security/cryptographic
purposes. Consider using the secrets module instead:
https://docs.python.org/library/secrets.html

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B311 | random              | - random.Random                    | Low       |
|      |                     | - random.random                    |           |
|      |                     | - random.randrange                 |           |
|      |                     | - random.randint                   |           |
|      |                     | - random.choice                    |           |
|      |                     | - random.choices                   |           |
|      |                     | - random.uniform                   |           |
|      |                     | - random.triangular                |           |
|      |                     | - random.randbytes                 |           |
|      |                     | - random.randrange                 |           |
|      |                     | - random.sample                    |           |
|      |                     | - random.getrandbits               |           |
+------+---------------------+------------------------------------+-----------+
