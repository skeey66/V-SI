---
id: cpython-pickle
title: Python pickle 모듈
source: https://raw.githubusercontent.com/python/cpython/3.11/Doc/library/pickle.rst
license: PSF License (Python documentation)
keys: 
---
## `pickle` --- Python object serialization

:synopsis: Convert Python objects to streams of bytes and back.

.. sectionauthor:: Jim Kerr <jbkerr@sr.hp.com>.
.. sectionauthor:: Barry Warsaw <barry@python.org>

**Source code:** `Lib/pickle.py`

   single: persistence
   pair: persistent; objects
   pair: serializing; objects
   pair: marshalling; objects
   pair: flattening; objects
   pair: pickling; objects

--------------

The `pickle` module implements binary protocols for serializing and
de-serializing a Python object structure.  *"Pickling"* is the process
whereby a Python object hierarchy is converted into a byte stream, and
*"unpickling"* is the inverse operation, whereby a byte stream
(from a `binary file` or `bytes-like object`) is converted
back into an object hierarchy.  Pickling (and unpickling) is alternatively
known as "serialization", "marshalling," [#]_ or "flattening"; however, to
avoid confusion, the terms used here are "pickling" and "unpickling".

.. warning::

   The ``pickle`` module **is not secure**. Only unpickle data you trust.

   It is possible to construct malicious pickle data which will **execute
   arbitrary code during unpickling**. Never unpickle data that could have come
   from an untrusted source, or that could have been tampered with.

   Consider signing data with `hmac` if you need to ensure that it has not
   been tampered with.

   Safer serialization formats such as `json` may be more appropriate if
   you are processing untrusted data. See `comparison-with-json`.

## Module Interface

To serialize an object hierarchy, you simply call the `dumps` function.
Similarly, to de-serialize a data stream, you call the `loads` function.
However, if you want more control over serialization and de-serialization,
you can create a `Pickler` or an `Unpickler` object, respectively.

The `pickle` module provides the following constants:

.. data:: HIGHEST_PROTOCOL

   An integer, the highest `protocol version`
   available.  This value can be passed as a *protocol* value to functions
   `dump` and `dumps` as well as the `Pickler`
   constructor.

.. data:: DEFAULT_PROTOCOL

   An integer, the default `protocol version` used
   for pickling.  May be less than `HIGHEST_PROTOCOL`.  Currently the
   default protocol is 4, first introduced in Python 3.4 and incompatible
   with previous versions.

   .. versionchanged:: 3.0

      The default protocol is 3.

   .. versionchanged:: 3.8

      The default protocol is 4.

The `pickle` module provides the following functions to make the pickling
process more convenient:

.. function:: dump(obj, file, protocol=None, *, fix_imports=True, buffer_callback=None)

   Write the pickled representation of the object *obj* to the open
   `file object` *file*.  This is equivalent to
   ``Pickler(file, protocol).dump(obj)``.

   Arguments *file*, *protocol*, *fix_imports* and *buffer_callback* have
   the same meaning as in the `Pickler` constructor.

   .. versionchanged:: 3.8
      The *buffer_callback* argument was added.

.. function:: dumps(obj, protocol=None, *, fix_imports=True, buffer_callback=None)

   Return the pickled representation of the object *obj* as a `bytes` object,
   instead of writing it to a file.

   Arguments *protocol*, *fix_imports* and *buffer_callback* have the same
   meaning as in the `Pickler` constructor.

   .. versionchanged:: 3.8
      The *buffer_callback* argument was added.

.. function:: load(file, *, fix_imports=True, encoding="ASCII", errors="strict", buffers=None)

   Read the pickled representation of an object from the open `file object`
   *file* and return the reconstituted object hierarchy specified therein.
   This is equivalent to ``Unpickler(file).load()``.

   The protocol version of the pickle is detected automatically, so no
   protocol argument is needed.  Bytes past the pickled representation
   of the object are ignored.

   Arguments *file*, *fix_imports*, *encoding*, *errors*, *strict* and *buffers*
   have the same meaning as in the `Unpickler` constructor.

   .. versionchanged:: 3.8
      The *buffers* argument was added.

.. function:: loads(data, /, *, fix_imports=True, encoding="ASCII", errors="strict", buffers=None)

   Return the reconstituted object hierarchy of the pickled representation
   *data* of an object. *data* must be a `bytes-like object`.

   The protocol version of the pickle is detected automatically, so no
   protocol argument is needed.  Bytes past the pickled representation
   of the object are ignored.

   Arguments *fix_imports*, *encoding*, *errors*, *strict* and *buffers*
   have the same meaning as in the `Unpickler` constructor.

   .. versionchanged:: 3.8
      The *buffers* argument was added.

The `pickle` module defines three exceptions:

.. exception:: PickleError

   Common base class for the other pickling exceptions.  It inherits from
   `Exception`.

.. exception:: PicklingError

   Error raised when an unpicklable object is encountered by `Pickler`.
   It inherits from `PickleError`.

   Refer to `pickle-picklable` to learn what kinds of objects can be
   pickled.

.. exception:: UnpicklingError

   Error raised when there is a problem unpickling an object, such as a data
   corruption or a security violation.  It inherits from `PickleError`.

   Note that other exceptions may also be raised during unpickling, including
   (but not necessarily limited to) AttributeError, EOFError, ImportError, and
   IndexError.

The `pickle` module exports three classes, `Pickler`,
`Unpickler` and `PickleBuffer`:

.. class:: Pickler(file, protocol=None, *, fix_imports=True, buffer_callback=None)

   This takes a binary file for writing a pickle data stream.

   The optional *protocol* argument, an integer, tells the pickler to use
   the given protocol; supported protocols are 0 to `HIGHEST_PROTOCOL`.
   If not specified, the default is `DEFAULT_PROTOCOL`.  If a negative
   number is specified, `HIGHEST_PROTOCOL` is selected.

   The *file* argument must have a write() method that accepts a single bytes
   argument.  It can thus be an on-disk file opened for binary writing, an
   `io.BytesIO` instance, or any other custom object that meets this
   interface.

   If *fix_imports* is true and *protocol* is less than 3, pickle will try to
   map the new Python 3 names to the old module names used in Python 2, so
   that the pickle data stream is readable with Python 2.

   If *buffer_callback* is None (the default), buffer views are
   serialized into *file* as part of the pickle stream.

   If *buffer_callback* is not None, then it can be called any number
   of times with a buffer view.  If the callback returns a false value
   (such as None), the given buffer is `out-of-band`;
   otherwise the buffer is serialized in-band, i.e. inside the pickle stream.

   It is an error if *buffer_callback* is not None and *protocol* is
   None or smaller than 5.

   .. versionchanged:: 3.8
      The *buffer_callback* argument was added.

   .. method:: dump(obj)

      Write the pickled representation of *obj* to the open file object given in
      the constructor.

   .. method:: persistent_id(obj)

      Do nothing by default.  This exists so a subclass can override it.

      If `persistent_id` returns ``None``, *obj* is pickled as usual.  Any
      other value causes `Pickler` to emit the returned value as a
      persistent ID for *obj*.  The meaning of this persistent ID should be
      defined by `Unpickler.persistent_load`.  Note that the value
      returned by `persistent_id` cannot itself have a persistent ID.

      See `pickle-persistent` for details and examples of uses.

   .. attribute:: dispatch_table

      A pickler object's dispatch table is a registry of *reduction
      functions* of the kind which can be declared using
      `copyreg.pickle`.  It is a mapping whose keys are classes
      and whose values are reduction functions.  A reduction function
      takes a single argument of the associated class and should
      conform to the same interface as a `object.__reduce__`
      method.

      By default, a pickler object will not have a
      `dispatch_table` attribute, and it will instead use the
      global dispatch table managed by the `copyreg` module.
      However, to customize the pickling for a specific pickler object
      one can set the `dispatch_table` attribute to a dict-like
      object.  Alternatively, if a subclass of `Pickler` has a
      `dispatch_table` attribute then this will be used as the
      default dispatch table for instances of that class.

      See `pickle-dispatch` for usage examples.

      .. versionadded:: 3.3

   .. method:: reducer_override(obj)

      Special reducer that can be defined in `Pickler` subclasses. This
      method has priority over any reducer in the `dispatch_table`.  It
      should conform to the same interface as a `object.__reduce__` method, and
      can optionally return `NotImplemented` to fallback on
      `dispatch_table`-registered reducers to pickle ``obj``.

      For a detailed example, see `reducer_override`.

      .. versionadded:: 3.8

   .. attribute:: fast

      Deprecated. Enable fast mode if set to a true value.  The fast mode
      disables the usage of memo, therefore speeding the pickling process by not
      generating superfluous PUT opcodes.  It should not be used with
      self-referential objects, doing otherwise will cause `Pickler` to
      recurse infinitely.

      Use `pickletools.optimize` if you need more compact pickles.

.. class:: Unpickler(file, *, fix_imports=True, encoding="ASCII", errors="strict", buffers=None)

   This takes a binary file for reading a pickle data stream.

   The protocol version of the pickle is detected automatically, so no
   protocol argument is needed.

   The argument *file* must have three methods, a read() method that takes an
   integer argument, a readinto() method that takes a buffer argument
   and a readline() method that requires no arguments, as in the
   `io.BufferedIOBase` interface.  Thus *file* can be an on-disk file
   opened for binary reading, an `io.BytesIO` object, or any other
   custom object that meets this interface.

   The optional arguments *fix_imports*, *encoding* and *errors* are used
   to control compatibility support for pickle stream generated by Python 2.
   If *fix_imports* is true, pickle will try to map the old Python 2 names
   to the new names used in Python 3.  The *encoding* and *errors* tell
   pickle how to decode 8-bit string instances pickled by Python 2;
   these default to 'ASCII' and 'strict', respectively.  The *encoding* can
   be 'bytes' to read these 8-bit string instances as bytes objects.
   Using ``encoding='latin1'`` is required for unpickling NumPy arrays and
   instances of `datetime.datetime`, `datetime.date` and
   `datetime.time` pickled by Python 2.

   If *buffers* is None (the default), then all data necessary for
   deserialization must be contained in the pickle stream.  This means
   that the *buffer_callback* argument was None when a `Pickler`
   was instantiated (or when `dump` or `dumps` was called).

   If *buffers* is not None, it should be an iterable of buffer-enabled
   objects that is consumed each time the pickle stream references
   an `out-of-band` buffer view.  Such buffers have been
   given in order to the *buffer_callback* of a Pickler object.

   .. versionchanged:: 3.8
      The *buffers* argument was added.

   .. method:: load()

      Read the pickled representation of an object from the open file object
      given in the constructor, and return the reconstituted object hierarchy
      specified therein.  Bytes past the pickled representation of the object
      are ignored.

   .. method:: persistent_load(pid)

      Raise an `UnpicklingError` by default.

      If defined, `persistent_load` should return the object specified by
      the persistent ID *pid*.  If an invalid persistent ID is encountered, an
      `UnpicklingError` should be raised.

      See `pickle-persistent` for details and examples of uses.

   .. method:: find_class(module, name)

      Import *module* if necessary and return the object called *name* from it,
      where the *module* and *name* arguments are `str` objects.  Note,
      unlike its name suggests, `find_class` is also used for finding
      functions.

      Subclasses may override this to gain control over what type of objects and
      how they can be loaded, potentially reducing security risks. Refer to
      `pickle-restrict` for details.

      .. audit-event:: pickle.find_class module,name pickle.Unpickler.find_class

.. class:: PickleBuffer(buffer)

   A wrapper for a buffer representing picklable data.  *buffer* must be a
   `buffer-providing` object, such as a
   `bytes-like object` or a N-dimensional array.

   `PickleBuffer` is itself a buffer provider, therefore it is
   possible to pass it to other APIs expecting a buffer-providing object,
   such as `memoryview`.

   `PickleBuffer` objects can only be serialized using pickle
   protocol 5 or higher.  They are eligible for
   `out-of-band serialization`.

   .. versionadded:: 3.8

   .. method:: raw()

      Return a `memoryview` of the memory area underlying this buffer.
      The returned object is a one-dimensional, C-contiguous memoryview
      with format ``B`` (unsigned bytes).  `BufferError` is raised if
      the buffer is neither C- nor Fortran-contiguous.

   .. method:: release()

      Release the underlying buffer exposed by the PickleBuffer object.

## Restricting Globals

single: find_class() (pickle protocol)

By default, unpickling will import any class or function that it finds in the
pickle data.  For many applications, this behaviour is unacceptable as it
permits the unpickler to import and invoke arbitrary code.  Just consider what
this hand-crafted pickle data stream does when loaded::

    >>> import pickle
    >>> pickle.loads(b"cos\nsystem\n(S'echo hello world'\ntR.")
    hello world
    0

In this example, the unpickler imports the `os.system` function and then
apply the string argument "echo hello world".  Although this example is
inoffensive, it is not difficult to imagine one that could damage your system.

For this reason, you may want to control what gets unpickled by customizing
`Unpickler.find_class`.  Unlike its name suggests,
`Unpickler.find_class` is called whenever a global (i.e., a class or
a function) is requested.  Thus it is possible to either completely forbid
globals or restrict them to a safe subset.

Here is an example of an unpickler allowing only few safe classes from the
`builtins` module to be loaded::

   import builtins
   import io
   import pickle

   safe_builtins = {
       'range',
       'complex',
       'set',
       'frozenset',
       'slice',
   }

   class RestrictedUnpickler(pickle.Unpickler):

       def find_class(self, module, name):
           # Only allow safe classes from builtins.
           if module == "builtins" and name in safe_builtins:
               return getattr(builtins, name)
           # Forbid everything else.
           raise pickle.UnpicklingError("global '%s.%s' is forbidden" %
                                        (module, name))

   def restricted_loads(s):
       """Helper function analogous to pickle.loads()."""
       return RestrictedUnpickler(io.BytesIO(s)).load()

A sample usage of our unpickler working as intended::

    >>> restricted_loads(pickle.dumps([1, 2, range(15)]))
    [1, 2, range(0, 15)]
    >>> restricted_loads(b"cos\nsystem\n(S'echo hello world'\ntR.")
    Traceback (most recent call last):
      ...
    pickle.UnpicklingError: global 'os.system' is forbidden
    >>> restricted_loads(b'cbuiltins\neval\n'
    ...                  b'(S\'getattr(__import__("os"), "system")'
    ...                  b'("echo hello world")\'\ntR.')
    Traceback (most recent call last):
      ...
    pickle.UnpicklingError: global 'builtins.eval' is forbidden

.. XXX Add note about how extension codes could evade our protection
   mechanism (e.g. cached classes do not invokes find_class()).

As our examples shows, you have to be careful with what you allow to be
unpickled.  Therefore if security is a concern, you may want to consider
alternatives such as the marshalling API in `xmlrpc.client` or
third-party solutions.
