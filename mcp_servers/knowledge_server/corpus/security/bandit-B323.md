---
id: bandit-B323
title: bandit B323 unverified_context
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B323, CWE-295
---
By default, Python will create a secure, verified ssl context for use in such classes as HTTPSConnection. However, it still allows using an insecure context via the _create_unverified_context that  reverts to the previous behavior that does not validate certificates or perform hostname checks.

대상 호출: ssl._create_unverified_context
심각도: MEDIUM · CWE-295

B323: unverified_context
------------------------

By default, Python will create a secure, verified ssl context for use in such
classes as HTTPSConnection. However, it still allows using an insecure
context via the _create_unverified_context that reverts to the previous
behavior that does not validate certificates or perform hostname checks.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B323 | unverified_context  | - ssl._create_unverified_context   | Medium    |
+------+---------------------+------------------------------------+-----------+
