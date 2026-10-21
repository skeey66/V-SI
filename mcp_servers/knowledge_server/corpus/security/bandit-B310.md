---
id: bandit-B310
title: bandit B310 urllib_urlopen
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B310, CWE-22
---
Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected.

대상 호출: urllib.request.urlopen, urllib.request.urlretrieve, urllib.request.URLopener, urllib.request.FancyURLopener, six.moves.urllib.request.urlopen, six.moves.urllib.request.urlretrieve, six.moves.urllib.request.URLopener, six.moves.urllib.request.FancyURLopener
심각도: MEDIUM · CWE-22

B310: urllib_urlopen
--------------------

Audit url open for permitted schemes. Allowing use of 'file:'' or custom
schemes is often unexpected.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B310 | urllib_urlopen      | - urllib.urlopen                   | Medium    |
|      |                     | - urllib.request.urlopen           |           |
|      |                     | - urllib.urlretrieve               |           |
|      |                     | - urllib.request.urlretrieve       |           |
|      |                     | - urllib.URLopener                 |           |
|      |                     | - urllib.request.URLopener         |           |
|      |                     | - urllib.FancyURLopener            |           |
|      |                     | - urllib.request.FancyURLopener    |           |
|      |                     | - urllib2.urlopen                  |           |
|      |                     | - urllib2.Request                  |           |
|      |                     | - six.moves.urllib.request.urlopen |           |
|      |                     | - six.moves.urllib.request         |           |
|      |                     |   .urlretrieve                     |           |
|      |                     | - six.moves.urllib.request         |           |
|      |                     |   .URLopener                       |           |
|      |                     | - six.moves.urllib.request         |           |
|      |                     |   .FancyURLopener                  |           |
+------+---------------------+------------------------------------+-----------+
