---
id: bandit-B303
title: bandit B303 md5
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B303, CWE-327
---
Use of insecure MD2, MD4, MD5, or SHA1 hash function.

대상 호출: Crypto.Hash.MD2.new, Crypto.Hash.MD4.new, Crypto.Hash.MD5.new, Crypto.Hash.SHA.new, Cryptodome.Hash.MD2.new, Cryptodome.Hash.MD4.new, Cryptodome.Hash.MD5.new, Cryptodome.Hash.SHA.new, cryptography.hazmat.primitives.hashes.MD5, cryptography.hazmat.primitives.hashes.SHA1
심각도: MEDIUM · CWE-327

B303: md5
---------

Use of insecure MD2, MD4, MD5, or SHA1 hash function.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B303 | md5                 | - hashlib.md5                      | Medium    |
|      |                     | - hashlib.sha1                     |           |
|      |                     | - Crypto.Hash.MD2.new              |           |
|      |                     | - Crypto.Hash.MD4.new              |           |
|      |                     | - Crypto.Hash.MD5.new              |           |
|      |                     | - Crypto.Hash.SHA.new              |           |
|      |                     | - Cryptodome.Hash.MD2.new          |           |
|      |                     | - Cryptodome.Hash.MD4.new          |           |
|      |                     | - Cryptodome.Hash.MD5.new          |           |
|      |                     | - Cryptodome.Hash.SHA.new          |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .hashes.MD5                      |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .hashes.SHA1                     |           |
+------+---------------------+------------------------------------+-----------+

B304 - B305: ciphers and modes
------------------------------

Use of insecure cipher or cipher mode. Replace with a known secure cipher such
as AES.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B304 | ciphers             | - Crypto.Cipher.ARC2.new           | High      |
|      |                     | - Crypto.Cipher.ARC4.new           |           |
|      |                     | - Crypto.Cipher.Blowfish.new       |           |
|      |                     | - Crypto.Cipher.DES.new            |           |
|      |                     | - Crypto.Cipher.XOR.new            |           |
|      |                     | - Cryptodome.Cipher.ARC2.new       |           |
|      |                     | - Cryptodome.Cipher.ARC4.new       |           |
|      |                     | - Cryptodome.Cipher.Blowfish.new   |           |
|      |                     | - Cryptodome.Cipher.DES.new        |           |
|      |                     | - Cryptodome.Cipher.XOR.new        |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .ciphers.algorithms.ARC4         |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .ciphers.algorithms.Blowfish     |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .ciphers.algorithms.IDEA         |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .ciphers.algorithms.CAST5        |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .ciphers.algorithms.SEED         |           |
|      |                     | - cryptography.hazmat.primitives   |           |
|      |                     |   .ciphers.algorithms.TripleDES    |           |
+------+---------------------+------------------------------------+-----------+
| B305 | cipher_modes        | - cryptography.hazmat.primitives   | Medium    |
|      |                     |   .ciphers.modes.ECB               |           |
+------+---------------------+------------------------------------+-----------+
