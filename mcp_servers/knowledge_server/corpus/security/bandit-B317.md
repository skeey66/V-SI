---
id: bandit-B317
title: bandit B317 xml_bad_sax
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B317, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Replace {name} with its defusedxml equivalent function or make sure defusedxml.defuse_stdlib() is called

대상 호출: xml.sax.parse, xml.sax.parseString, xml.sax.make_parser
심각도: MEDIUM · CWE-20
