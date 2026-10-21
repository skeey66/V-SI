---
id: bandit-B314
title: bandit B314 xml_bad_ElementTree
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B314, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with its defusedxml equivalent function or make sure defusedxml.defuse_stdlib() is called

대상 호출: xml.etree.ElementTree.parse, xml.etree.ElementTree.iterparse, xml.etree.ElementTree.fromstring, xml.etree.ElementTree.XMLParser
심각도: MEDIUM · CWE-20
