---
id: cpython-ast
title: Python ast 모듈
source: https://raw.githubusercontent.com/python/cpython/3.11/Doc/library/ast.rst
license: PSF License (Python documentation)
keys: 
---
## `ast` Helpers

Apart from the node classes, the `ast` module defines these utility functions
and classes for traversing abstract syntax trees:

.. function:: parse(source, filename='<unknown>', mode='exec', *, type_comments=False, feature_version=None)

   Parse the source into an AST node.  Equivalent to ``compile(source,
   filename, mode, ast.PyCF_ONLY_AST)``.

   If ``type_comments=True`` is given, the parser is modified to check
   and return type comments as specified by `484` and `526`.
   This is equivalent to adding `ast.PyCF_TYPE_COMMENTS` to the
   flags passed to `compile()`.  This will report syntax errors
   for misplaced type comments.  Without this flag, type comments will
   be ignored, and the ``type_comment`` field on selected AST nodes
   will always be ``None``.  In addition, the locations of ``# type:
   ignore`` comments will be returned as the ``type_ignores``
   attribute of `Module` (otherwise it is always an empty list).

   In addition, if ``mode`` is ``'func_type'``, the input syntax is
   modified to correspond to `484` "signature type comments",
   e.g. ``(str, int) -> List[str]``.

   Setting ``feature_version`` to a tuple ``(major, minor)`` will result in
   a "best-effort" attempt to parse using that Python version's grammar.
   For example, setting ``feature_version=(3, 9)`` will attempt to disallow
   parsing of `match` statements.
   Currently ``major`` must equal to ``3``. The lowest supported version is
   ``(3, 4)`` (and this may increase in future Python versions);
   the highest is ``sys.version_info[0:2]``. "Best-effort" attempt means there
   is no guarantee that the parse (or success of the parse) is the same as
   when run on the Python version corresponding to ``feature_version``.

   If source contains a null character (``\0``), `ValueError` is raised.

   .. warning::
      Note that successfully parsing source code into an AST object doesn't
      guarantee that the source code provided is valid Python code that can
      be executed as the compilation step can raise further `SyntaxError`
      exceptions. For instance, the source ``return 42`` generates a valid
      AST node for a return statement, but it cannot be compiled alone (it needs
      to be inside a function node).

      In particular, `ast.parse` won't do any scoping checks, which the
      compilation step does.

   .. warning::
      It is possible to crash the Python interpreter with a
      sufficiently large/complex string due to stack depth limitations
      in Python's AST compiler.

   .. versionchanged:: 3.8
      Added ``type_comments``, ``mode='func_type'`` and ``feature_version``.

.. function:: unparse(ast_obj)

   Unparse an `ast.AST` object and generate a string with code
   that would produce an equivalent `ast.AST` object if parsed
   back with `ast.parse`.

   .. warning::
      The produced code string will not necessarily be equal to the original
      code that generated the `ast.AST` object (without any compiler
      optimizations, such as constant tuples/frozensets).

   .. warning::
      Trying to unparse a highly complex expression would result with
      `RecursionError`.

   .. versionadded:: 3.9

.. function:: literal_eval(node_or_string)

   Evaluate an expression node or a string containing only a Python literal or
   container display.  The string or node provided may only consist of the
   following Python literal structures: strings, bytes, numbers, tuples, lists,
   dicts, sets, booleans, ``None`` and ``Ellipsis``.

   This can be used for evaluating strings containing Python values without the
   need to parse the values oneself.  It is not capable of evaluating
   arbitrarily complex expressions, for example involving operators or
   indexing.

   This function had been documented as "safe" in the past without defining
   what that meant. That was misleading. This is specifically designed not to
   execute Python code, unlike the more general `eval`. There is no
   namespace, no name lookups, or ability to call out. But it is not free from
   attack: A relatively small input can lead to memory exhaustion or to C stack
   exhaustion, crashing the process. There is also the possibility for
   excessive CPU consumption denial of service on some inputs. Calling it on
   untrusted data is thus not recommended.

   .. warning::
      It is possible to crash the Python interpreter due to stack depth
      limitations in Python's AST compiler.

      It can raise `ValueError`, `TypeError`, `SyntaxError`,
      `MemoryError` and `RecursionError` depending on the malformed
      input.

   .. versionchanged:: 3.2
      Now allows bytes and set literals.

   .. versionchanged:: 3.9
      Now supports creating empty sets with ``'set()'``.

   .. versionchanged:: 3.10
      For string inputs, leading spaces and tabs are now stripped.

.. function:: get_docstring(node, clean=True)

   Return the docstring of the given *node* (which must be a
   `FunctionDef`, `AsyncFunctionDef`, `ClassDef`,
   or `Module` node), or ``None`` if it has no docstring.
   If *clean* is true, clean up the docstring's indentation with
   `inspect.cleandoc`.

   .. versionchanged:: 3.5
      `AsyncFunctionDef` is now supported.

.. function:: get_source_segment(source, node, *, padded=False)

   Get source code segment of the *source* that generated *node*.
   If some location information (`ast.AST.lineno`, `ast.AST.end_lineno`,
   `ast.AST.col_offset`, or `ast.AST.end_col_offset`) is missing, return ``None``.

   If *padded* is ``True``, the first line of a multi-line statement will
   be padded with spaces to match its original position.

   .. versionadded:: 3.8

.. function:: fix_missing_locations(node)

   When you compile a node tree with `compile`, the compiler expects
   `ast.AST.lineno` and `ast.AST.col_offset` attributes for every node that supports
   them.  This is rather tedious to fill in for generated nodes, so this helper
   adds these attributes recursively where not already set, by setting them to
   the values of the parent node.  It works recursively starting at *node*.

.. function:: increment_lineno(node, n=1)

   Increment the line number and end line number of each node in the tree
   starting at *node* by *n*. This is useful to "move code" to a different
   location in a file.

.. function:: copy_location(new_node, old_node)

   Copy source location (`ast.AST.lineno`, `ast.AST.col_offset`, `ast.AST.end_lineno`,
   and `ast.AST.end_col_offset`) from *old_node* to *new_node* if possible,
   and return *new_node*.

.. function:: iter_fields(node)

   Yield a tuple of ``(fieldname, value)`` for each field in ``node._fields``
   that is present on *node*.

.. function:: iter_child_nodes(node)

   Yield all direct child nodes of *node*, that is, all fields that are nodes
   and all items of fields that are lists of nodes.

.. function:: walk(node)

   Recursively yield all descendant nodes in the tree starting at *node*
   (including *node* itself), in no specified order.  This is useful if you only
   want to modify nodes in place and don't care about the context.

.. class:: NodeVisitor()

   A node visitor base class that walks the abstract syntax tree and calls a
   visitor function for every node found.  This function may return a value
   which is forwarded by the `visit` method.

   This class is meant to be subclassed, with the subclass adding visitor
   methods.

   .. method:: visit(node)

      Visit a node.  The default implementation calls the method called
      `self.visit_{classname}` where *classname* is the name of the node
      class, or `generic_visit` if that method doesn't exist.

   .. method:: generic_visit(node)

      This visitor calls `visit` on all children of the node.

      Note that child nodes of nodes that have a custom visitor method won't be
      visited unless the visitor calls `generic_visit` or visits them
      itself.

   .. method:: visit_Constant(node)

      Handles all constant nodes.

   Don't use the `NodeVisitor` if you want to apply changes to nodes
   during traversal.  For this a special visitor exists
   (`NodeTransformer`) that allows modifications.

   .. deprecated:: 3.8

      Methods `!visit_Num`, `!visit_Str`, `!visit_Bytes`,
      `!visit_NameConstant` and `!visit_Ellipsis` are deprecated
      now and will not be called in future Python versions.  Add the
      `visit_Constant` method to handle all constant nodes.

.. class:: NodeTransformer()

   A `NodeVisitor` subclass that walks the abstract syntax tree and
   allows modification of nodes.

   The `NodeTransformer` will walk the AST and use the return value of
   the visitor methods to replace or remove the old node.  If the return value
   of the visitor method is ``None``, the node will be removed from its
   location, otherwise it is replaced with the return value.  The return value
   may be the original node in which case no replacement takes place.

   Here is an example transformer that rewrites all occurrences of name lookups
   (``foo``) to ``data['foo']``::

      class RewriteName(NodeTransformer):

          def visit_Name(self, node):
              return Subscript(
                  value=Name(id='data', ctx=Load()),
                  slice=Constant(value=node.id),
                  ctx=node.ctx
              )

   Keep in mind that if the node you're operating on has child nodes you must
   either transform the child nodes yourself or call the `ast.NodeVisitor.generic_visit`
   method for the node first.

   For nodes that were part of a collection of statements (that applies to all
   statement nodes), the visitor may also return a list of nodes rather than
   just a single node.

   If `NodeTransformer` introduces new nodes (that weren't part of
   original tree) without giving them location information (such as
   `ast.AST.lineno`), `fix_missing_locations` should be called with
   the new sub-tree to recalculate the location information::

      tree = ast.parse('foo', mode='eval')
      new_tree = fix_missing_locations(RewriteName().visit(tree))

   Usually you use the transformer like this::

      node = YourTransformer().visit(node)

.. function:: dump(node, annotate_fields=True, include_attributes=False, *, indent=None)

   Return a formatted dump of the tree in *node*.  This is mainly useful for
   debugging purposes.  If *annotate_fields* is true (by default),
   the returned string will show the names and the values for fields.
   If *annotate_fields* is false, the result string will be more compact by
   omitting unambiguous field names.  Attributes such as line
   numbers and column offsets are not dumped by default.  If this is wanted,
   *include_attributes* can be set to true.

   If *indent* is a non-negative integer or string, then the tree will be
   pretty-printed with that indent level.  An indent level
   of 0, negative, or ``""`` will only insert newlines.  ``None`` (the default)
   selects the single line representation. Using a positive integer indent
   indents that many spaces per level.  If *indent* is a string (such as ``"\t"``),
   that string is used to indent each level.

   .. versionchanged:: 3.9
      Added the *indent* option.
