---
id: bandit-B301
title: bandit B301 pickle
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B301, CWE-502
---
Pickle and modules that wrap it can be unsafe when used to deserialize untrusted data, possible security issue.

대상 호출: pickle.loads, pickle.load, pickle.Unpickler, dill.loads, dill.load, dill.Unpickler, shelve.open, shelve.DbfilenameShelf, jsonpickle.decode, jsonpickle.unpickler.decode, jsonpickle.unpickler.Unpickler, pandas.read_pickle
심각도: MEDIUM · CWE-502

B301: pickle
------------

Pickle and modules that wrap it can be unsafe when used to
deserialize untrusted data, possible security issue.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B301 | pickle              | - pickle.loads                     | Medium    |
|      |                     | - pickle.load                      |           |
|      |                     | - pickle.Unpickler                 |           |
|      |                     | - dill.loads                       |           |
|      |                     | - dill.load                        |           |
|      |                     | - dill.Unpickler                   |           |
|      |                     | - shelve.open                      |           |
|      |                     | - shelve.DbfilenameShelf           |           |
|      |                     | - jsonpickle.decode                |           |
|      |                     | - jsonpickle.unpickler.decode      |           |
|      |                     | - jsonpickle.unpickler.Unpickler   |           |
|      |                     | - pandas.read_pickle               |           |
+------+---------------------+------------------------------------+-----------+
