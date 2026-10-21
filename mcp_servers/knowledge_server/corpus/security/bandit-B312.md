---
id: bandit-B312
title: bandit B312 telnetlib
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B312, CWE-319
---
Telnet-related functions are being called. Telnet is considered insecure. Use SSH or some other encrypted protocol.

대상 호출: telnetlib.Telnet
심각도: HIGH · CWE-319

B312: telnetlib
---------------

Telnet-related functions are being called. Telnet is considered insecure. Use
SSH or some other encrypted protocol.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B312 | telnetlib           | - telnetlib.\*                     | High      |
+------+---------------------+------------------------------------+-----------+

B313 - B319: XML
----------------

Most of this is based off of Christian Heimes' work on defusedxml:
https://pypi.org/project/defusedxml/#defusedxml-sax

Using various XLM methods to parse untrusted XML data is known to be vulnerable
to XML attacks. Methods should be replaced with their defusedxml equivalents.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B313 | xml_bad_cElementTree| - xml.etree.cElementTree.parse     | Medium    |
|      |                     | - xml.etree.cElementTree.iterparse |           |
|      |                     | - xml.etree.cElementTree.fromstring|           |
|      |                     | - xml.etree.cElementTree.XMLParser |           |
+------+---------------------+------------------------------------+-----------+
| B314 | xml_bad_ElementTree | - xml.etree.ElementTree.parse      | Medium    |
|      |                     | - xml.etree.ElementTree.iterparse  |           |
|      |                     | - xml.etree.ElementTree.fromstring |           |
|      |                     | - xml.etree.ElementTree.XMLParser  |           |
+------+---------------------+------------------------------------+-----------+
| B315 | xml_bad_expatreader | - xml.sax.expatreader.create_parser| Medium    |
+------+---------------------+------------------------------------+-----------+
| B316 | xml_bad_expatbuilder| - xml.dom.expatbuilder.parse       | Medium    |
|      |                     | - xml.dom.expatbuilder.parseString |           |
+------+---------------------+------------------------------------+-----------+
| B317 | xml_bad_sax         | - xml.sax.parse                    | Medium    |
|      |                     | - xml.sax.parseString              |           |
|      |                     | - xml.sax.make_parser              |           |
+------+---------------------+------------------------------------+-----------+
| B318 | xml_bad_minidom     | - xml.dom.minidom.parse            | Medium    |
|      |                     | - xml.dom.minidom.parseString      |           |
+------+---------------------+------------------------------------+-----------+
| B319 | xml_bad_pulldom     | - xml.dom.pulldom.parse            | Medium    |
|      |                     | - xml.dom.pulldom.parseString      |           |
+------+---------------------+------------------------------------+-----------+
