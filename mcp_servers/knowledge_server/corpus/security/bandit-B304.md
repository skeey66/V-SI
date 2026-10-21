---
id: bandit-B304
title: bandit B304 ciphers
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B304, CWE-327
---
Use of insecure cipher {name}. Replace with a known secure cipher such as AES.

대상 호출: Crypto.Cipher.ARC2.new, Crypto.Cipher.ARC4.new, Crypto.Cipher.Blowfish.new, Crypto.Cipher.DES.new, Crypto.Cipher.XOR.new, Cryptodome.Cipher.ARC2.new, Cryptodome.Cipher.ARC4.new, Cryptodome.Cipher.Blowfish.new, Cryptodome.Cipher.DES.new, Cryptodome.Cipher.XOR.new, cryptography.hazmat.primitives.ciphers.algorithms.ARC4, cryptography.hazmat.primitives.ciphers.algorithms.Blowfish, cryptography.hazmat.primitives.ciphers.algorithms.CAST5, cryptography.hazmat.primitives.ciphers.algorithms.IDEA, cryptography.hazmat.primitives.ciphers.algorithms.SEED, cryptography.hazmat.primitives.ciphers.algorithms.TripleDES
심각도: HIGH · CWE-327
