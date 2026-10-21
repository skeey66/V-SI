---
id: bandit-B313
title: bandit B313 xml_bad_cElementTree
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B313, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with its defusedxml equivalent function or make sure defusedxml.defuse_stdlib() is called

대상 호출: xml.etree.cElementTree.parse, xml.etree.cElementTree.iterparse, xml.etree.cElementTree.fromstring, xml.etree.cElementTree.XMLParser
심각도: MEDIUM · CWE-20
