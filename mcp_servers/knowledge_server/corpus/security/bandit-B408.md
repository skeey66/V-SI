---
id: bandit-B408
title: bandit B408 import_xml_minidom
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B408, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with the equivalent defusedxml package, or make sure defusedxml.defuse_stdlib() is called.

대상 호출: xml.dom.minidom
심각도: LOW · CWE-20

B408: import_xml_minidom
------------------------

Using various methods to parse untrusted XML data is known to be vulnerable to
XML attacks. Replace vulnerable imports with the equivalent defusedxml package,
or make sure defusedxml.defuse_stdlib() is called.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B408 | import_xml_minidom  | - xml.dom.minidom                  | low       |
+------+---------------------+------------------------------------+-----------+
