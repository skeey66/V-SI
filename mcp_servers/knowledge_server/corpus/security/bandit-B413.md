---
id: bandit-B413
title: bandit B413 import_pycrypto
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B413, CWE-327
---
The pyCrypto library and its module {name} are no longer actively maintained and have been deprecated. Consider using pyca/cryptography library.

대상 호출: Crypto.Cipher, Crypto.Hash, Crypto.IO, Crypto.Protocol, Crypto.PublicKey, Crypto.Random, Crypto.Signature, Crypto.Util
심각도: HIGH · CWE-327

B413: import_pycrypto
---------------------
pycrypto library is known to have publicly disclosed buffer overflow
vulnerability https://github.com/dlitz/pycrypto/issues/176. It is no longer
actively maintained and has been deprecated in favor of pyca/cryptography
library.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B413 | import_pycrypto     | - Crypto.Cipher                    | high      |
|      |                     | - Crypto.Hash                      |           |
|      |                     | - Crypto.IO                        |           |
|      |                     | - Crypto.Protocol                  |           |
|      |                     | - Crypto.PublicKey                 |           |
|      |                     | - Crypto.Random                    |           |
|      |                     | - Crypto.Signature                 |           |
|      |                     | - Crypto.Util                      |           |
+------+---------------------+------------------------------------+-----------+
