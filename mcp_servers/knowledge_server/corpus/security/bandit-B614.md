---
id: bandit-B614
title: bandit B614 pytorch_load
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B614
---
This plugin checks for unsafe use of `torch.load` and
`torch.serialization.load`. Using `torch.load` or
`torch.serialization.load` with untrusted data can lead to
arbitrary code execution. The safe alternative is to use
`weights_only=True` or the safetensors library.
