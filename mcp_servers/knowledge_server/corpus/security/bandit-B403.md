---
id: bandit-B403
title: bandit B403 import_pickle
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B403, CWE-502
---
Consider possible security implications associated with {name} module.

대상 호출: pickle, cPickle, dill, shelve
심각도: LOW · CWE-502

B403: import_pickle
-------------------

Consider possible security implications associated with these modules.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B403 | import_pickle       | - pickle                           | low       |
|      |                     | - cPickle                          |           |
|      |                     | - dill                             |           |
|      |                     | - shelve                           |           |
+------+---------------------+------------------------------------+-----------+
