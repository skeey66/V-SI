---
id: pytest-how-to-assert-raises
title: pytest 예외·근사값 검사
source: https://raw.githubusercontent.com/pytest-dev/pytest/main/doc/en/how-to/assert.rst
license: MIT (pytest-dev)
keys: 
---
## Assertions about approximate equality

When comparing floating point values (or arrays of floats), small rounding
errors are common. Instead of using ``assert abs(a - b) < tol`` or
``numpy.isclose``, you can use `pytest.approx`:

```

    import pytest
    import numpy as np

    def test_floats():
        assert (0.1 + 0.2) == pytest.approx(0.3)

    def test_arrays():
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([0.9999, 2.0001, 3.0])
        assert a == pytest.approx(b)

``pytest.approx`` works with scalars, lists, dictionaries, and NumPy arrays.
It also supports comparisons involving NaNs.

See `pytest.approx` for details.

## Assertions about expected exceptions

In order to write assertions about raised exceptions, you can use
`pytest.raises` as a context manager like this:

```

    import pytest

    def test_zero_division():
        with pytest.raises(ZeroDivisionError):
            1 / 0

and if you need to have access to the actual exception info you may use:

```

    def test_recursion_depth():
        with pytest.raises(RuntimeError) as excinfo:

            def f():
                f()

            f()
        assert "maximum recursion" in str(excinfo.value)

``excinfo`` is an `pytest.ExceptionInfo` instance, which is a wrapper around
the actual exception raised.  The main attributes of interest are
``.type``, ``.value`` and ``.traceback``.

Note that ``pytest.raises`` will match the exception type or any subclasses (like the standard ``except`` statement).
If you want to check if a block of code is raising an exact exception type, you need to check that explicitly:

```

    def test_foo_not_implemented():
        def foo():
            raise NotImplementedError

        with pytest.raises(RuntimeError) as excinfo:
            foo()
        assert excinfo.type is RuntimeError

The `pytest.raises` call will succeed, even though the function raises `NotImplementedError`, because
`NotImplementedError` is a subclass of `RuntimeError`; however the following `assert` statement will
catch the problem.

## Matching exception messages

You can pass a ``match`` keyword parameter to the context-manager to test
that a regular expression matches on the string representation of an exception
(similar to the ``TestCase.assertRaisesRegex`` method from ``unittest``):

```

    import pytest

    def myfunc():
        raise ValueError("Exception 123 raised")

    def test_match():
        with pytest.raises(ValueError, match=r".* 123 .*"):
            myfunc()

Notes:

* The ``match`` parameter is matched with the `re.search`
  function, so in the above example ``match='123'`` would have worked as well.
* The ``match`` parameter also matches against `PEP-678 <https://peps.python.org/pep-0678/>`__ ``__notes__``.

## Assertions about expected exception groups

When expecting a `BaseExceptionGroup` or `ExceptionGroup` you can use `pytest.RaisesGroup`:

```

    def test_exception_in_group():
        with pytest.RaisesGroup(ValueError):
            raise ExceptionGroup("group msg", [ValueError("value msg")])
        with pytest.RaisesGroup(ValueError, TypeError):
            raise ExceptionGroup("msg", [ValueError("foo"), TypeError("bar")])

It accepts a ``match`` parameter, that checks against the group message, and a ``check`` parameter that takes an arbitrary callable which it passes the group to, and only succeeds if the callable returns ``True``.

```

    def test_raisesgroup_match_and_check():
        with pytest.RaisesGroup(BaseException, match="my group msg"):
            raise BaseExceptionGroup("my group msg", [KeyboardInterrupt()])
        with pytest.RaisesGroup(
            Exception, check=lambda eg: isinstance(eg.__cause__, ValueError)
        ):
            raise ExceptionGroup("", [TypeError()]) from ValueError()

It is strict about structure and unwrapped exceptions, unlike `except*`, so you might want to set the ``flatten_subgroups`` and/or ``allow_unwrapped`` parameters.

```

    def test_structure():
        with pytest.RaisesGroup(pytest.RaisesGroup(ValueError)):
            raise ExceptionGroup("", (ExceptionGroup("", (ValueError(),)),))
        with pytest.RaisesGroup(ValueError, flatten_subgroups=True):
            raise ExceptionGroup("1st group", [ExceptionGroup("2nd group", [ValueError()])])
        with pytest.RaisesGroup(ValueError, allow_unwrapped=True):
            raise ValueError

To specify more details about the contained exception you can use `pytest.RaisesExc`

```

    def test_raises_exc():
        with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match="foo")):
            raise ExceptionGroup("", (ValueError("foo")))

They both supply a method `pytest.RaisesGroup.matches` `pytest.RaisesExc.matches` if you want to do matching outside of using it as a :external+python`context manager`. This can be helpful when checking ``.__context__`` or ``.__cause__``.

```

    def test_matches():
        exc = ValueError()
        exc_group = ExceptionGroup("", [exc])
        if RaisesGroup(ValueError).matches(exc_group):
            ...
        # helpful error is available in `.fail_reason` if it fails to match
        r = RaisesExc(ValueError)
        assert r.matches(e), r.fail_reason

Check the documentation on `pytest.RaisesGroup` and `pytest.RaisesExc` for more details and examples.

## ``ExceptionInfo.group_contains()``

.. warning::

   This helper makes it easy to check for the presence of specific exceptions, but it is very bad for checking that the group does *not* contain *any other exceptions*. So this will pass:

    .. code-block:: python

       class EXTREMELYBADERROR(BaseException):
           """This is a very bad error to miss"""

       def test_for_value_error():
           with pytest.raises(ExceptionGroup) as excinfo:
               excs = [ValueError()]
               if very_unlucky():
                   excs.append(EXTREMELYBADERROR())
               raise ExceptionGroup("", excs)
           # This passes regardless of if there's other exceptions.
           assert excinfo.group_contains(ValueError)
           # You can't simply list all exceptions you *don't* want to get here.

   There is no good way of using `excinfo.group_contains()` to ensure you're not getting *any* other exceptions than the one you expected.
   You should instead use `pytest.RaisesGroup`, see `assert-matching-exception-groups`.

You can also use the `excinfo.group_contains()`
method to test for exceptions returned as part of an `ExceptionGroup`:

```

    def test_exception_in_group():
        with pytest.raises(ExceptionGroup) as excinfo:
            raise ExceptionGroup(
                "Group message",
                [
                    RuntimeError("Exception 123 raised"),
                ],
            )
        assert excinfo.group_contains(RuntimeError, match=r".* 123 .*")
        assert not excinfo.group_contains(TypeError)

The optional ``match`` keyword parameter works the same way as for
`pytest.raises`.

By default ``group_contains()`` will recursively search for a matching
exception at any level of nested ``ExceptionGroup`` instances. You can
specify a ``depth`` keyword parameter if you only want to match an
exception at a specific level; exceptions contained directly in the top
``ExceptionGroup`` would match ``depth=1``.

```

    def test_exception_in_group_at_given_depth():
        with pytest.raises(ExceptionGroup) as excinfo:
            raise ExceptionGroup(
                "Group message",
                [
                    RuntimeError(),
                    ExceptionGroup(
                        "Nested group",
                        [
                            TypeError(),
                        ],
                    ),
                ],
            )
        assert excinfo.group_contains(RuntimeError, depth=1)
        assert excinfo.group_contains(TypeError, depth=2)
        assert not excinfo.group_contains(RuntimeError, depth=2)
        assert not excinfo.group_contains(TypeError, depth=1)

## Alternate `pytest.raises` form (legacy)

There is an alternate form of `pytest.raises` where you pass
a function that will be executed, along with ``*args`` and ``**kwargs``. `pytest.raises`
will then execute the function with those arguments and assert that the given exception is raised:

```

    def func(x):
        if x <= 0:
            raise ValueError("x needs to be larger than zero")

    pytest.raises(ValueError, func, x=-1)

This form was the original `pytest.raises` API, developed before the ``with`` statement was
added to the Python language. Nowadays, this form is rarely used, with the context-manager form (using ``with``)
being considered more readable.

## xfail mark and pytest.raises

It is also possible to specify a ``raises`` argument to
`pytest.mark.xfail`, which checks that the test is failing in a more
specific way than just having any exception raised:

```

    def f():
        raise IndexError()

    @pytest.mark.xfail(raises=IndexError)
    def test_f():
        f()

This will only "xfail" if the test fails by raising ``IndexError`` or subclasses.

* Using `pytest.mark.xfail` with the ``raises`` parameter is probably better for something
  like documenting unfixed bugs (where the test describes what "should" happen) or bugs in dependencies.

* Using `pytest.raises` is likely to be better for cases where you are
  testing exceptions your own code is deliberately raising, which is the majority of cases.

You can also use `pytest.RaisesGroup`:

```

    def f():
        raise ExceptionGroup("", [IndexError()])

    @pytest.mark.xfail(raises=RaisesGroup(IndexError))
    def test_f():
        f()

## Assertions about expected warnings

You can check that code raises a particular warning using
`pytest.warns`.

## Returning non-None value in test functions

A `pytest.PytestReturnNotNoneWarning` is emitted when a test function returns a value other than ``None``.

This helps prevent a common mistake made by beginners who assume that returning a ``bool`` (e.g., ``True`` or ``False``) will determine whether a test passes or fails.

Example:

```

    @pytest.mark.parametrize(
        ["a", "b", "result"],
        [
            [1, 2, 5],
            [2, 3, 8],
            [5, 3, 18],
        ],
    )
    def test_foo(a, b, result):
        return foo(a, b) == result  # Incorrect usage, do not do this.

Since pytest ignores return values, it might be surprising that the test will never fail based on the returned value.

The correct fix is to replace the ``return`` statement with an ``assert``:

```

    @pytest.mark.parametrize(
        ["a", "b", "result"],
        [
            [1, 2, 5],
            [2, 3, 8],
            [5, 3, 18],
        ],
    )
    def test_foo(a, b, result):
        assert foo(a, b) == result
