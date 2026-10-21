---
id: bandit-B318
title: bandit B318 xml_bad_minidom
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B318, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with its defusedxml equivalent function or make sure defusedxml.defuse_stdlib() is called

대상 호출: xml.dom.minidom.parse, xml.dom.minidom.parseString
심각도: MEDIUM · CWE-20
