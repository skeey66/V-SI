---
id: bandit-B406
title: bandit B406 import_xml_sax
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B406, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with the equivalent defusedxml package, or make sure defusedxml.defuse_stdlib() is called.

대상 호출: xml.sax
심각도: LOW · CWE-20

B406: import_xml_sax
--------------------

Using various methods to parse untrusted XML data is known to be vulnerable to
XML attacks. Replace vulnerable imports with the equivalent defusedxml package,
or make sure defusedxml.defuse_stdlib() is called.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B406 | import_xml_sax      | - xml.sax                          | low       |
+------+---------------------+------------------------------------+-----------+
