---
id: bandit-B405
title: bandit B405 import_xml_etree
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B405, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with the equivalent defusedxml package, or make sure defusedxml.defuse_stdlib() is called.

대상 호출: xml.etree.cElementTree, xml.etree.ElementTree
심각도: LOW · CWE-20

B405: import_xml_etree
----------------------

Using various methods to parse untrusted XML data is known to be vulnerable to
XML attacks. Replace vulnerable imports with the equivalent defusedxml package,
or make sure defusedxml.defuse_stdlib() is called.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B405 | import_xml_etree    | - xml.etree.cElementTree           | low       |
|      |                     | - xml.etree.ElementTree            |           |
+------+---------------------+------------------------------------+-----------+
