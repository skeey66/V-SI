---
id: owasp-deserialization-cheat-sheet
title: OWASP 역직렬화
source: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Deserialization_Cheat_Sheet.md
license: CC BY-SA 4.0 (OWASP Cheat Sheet Series)
keys: 
---
## Introduction

This article is focused on providing clear, actionable guidance for safely deserializing untrusted data in your applications.

## Guidance on Deserializing Objects Safely

The following language-specific guidance attempts to enumerate safe methodologies for deserializing data that can't be trusted.

## Clear-box Review

Review the following APIs for untrusted input:

1. Uses of [`pickle.load()` or `pickle.loads()`](https://docs.python.org/3/library/pickle.html#pickle.loads). Unpickling can execute arbitrary code; never unpickle untrusted data. `pickle.loads()` expects bytes-like input, not a Python 3 string.

2. Uses of [PyYAML's unsafe loaders](https://github.com/yaml/pyyaml/blob/6.0.3/lib/yaml/__init__.py#L74-L156), such as `yaml.unsafe_load()` or `yaml.load()` with `Loader=yaml.Loader` or `Loader=yaml.UnsafeLoader`. For untrusted YAML, use [`yaml.safe_load()`](https://pyyaml.org/wiki/PyYAMLDocumentation#the-yaml-package), and do not register custom constructors that allow arbitrary object creation. Current PyYAML requires an explicit `Loader` argument for `yaml.load()`.

3. Uses of [`jsonpickle.decode()`](https://jsonpickle.readthedocs.io/en/latest/api.html#jsonpickle.decode) with untrusted input.

## General Precautions

Microsoft has stated that the `BinaryFormatter` type is dangerous and cannot be secured. As such, it should not be used. Full details are in the [BinaryFormatter security guide](https://docs.microsoft.com/en-us/dotnet/standard/serialization/binaryformatter-security-guide).

Don't allow the datastream to define the type of object that the stream will be deserialized to. You can prevent this by for example using the `DataContractSerializer` or `XmlSerializer` if at all possible.

Where `JSON.Net` is being used make sure the `TypeNameHandling` is only set to `None`.

```csharp
TypeNameHandling = TypeNameHandling.None
```

If `JavaScriptSerializer` is to be used then do not use it with a `JavaScriptTypeResolver`.

If you must deserialize data streams that define their own type, then restrict the types that are allowed to be deserialized. One should be aware that this is still risky as many native .Net types potentially dangerous in themselves. e.g.

```csharp
System.IO.FileInfo
```

`FileInfo` objects that reference files actually on the server can when deserialized, change the properties of those files e.g. to read-only, creating a potential denial of service attack.

Even if you have limited the types that can be deserialized remember that some types have properties that are risky. `System.ComponentModel.DataAnnotations.ValidationException`, for example has a property `Value` of type `Object`. if this type is the type allowed for deserialization then an attacker can set the `Value` property to any object type they choose.

Attackers should be prevented from steering the type that will be instantiated. If this is possible then even `DataContractSerializer` or `XmlSerializer` can be subverted e.g.

```csharp
// Action below is dangerous if the attacker can change the data in the database
var typename = GetTransactionTypeFromDatabase();

var serializer = new DataContractJsonSerializer(Type.GetType(typename));

var obj = serializer.ReadObject(ms);
```

Execution can occur within certain .Net types during deserialization. Creating a control such as the one shown below is ineffective.

```csharp
var suspectObject = myBinaryFormatter.Deserialize(untrustedData);

//Check below is too late! Execution may have already occurred.
if (suspectObject is SomeDangerousObjectType)
{
    //generate warnings and dispose of suspectObject
}
```

For `JSON.Net` it is possible to create a safer form of allow-list control using a custom `SerializationBinder`.

Try to keep up-to-date on known .Net insecure deserialization gadgets and pay special attention where such types can be created by your deserialization processes. **A deserializer can only instantiate types that it knows about**.

Try to keep any code that might create potential gadgets separate from any code that has internet connectivity. As an example `System.Windows.Data.ObjectDataProvider` used in WPF applications is a known gadget that allows arbitrary method invocation. It would be risky to have this a reference to this assembly in a REST service project that deserializes untrusted data.

## References

- [Oracle Java 17: Serialization Filtering](https://docs.oracle.com/en/java/javase/17/core/serialization-filtering1.html)
- [Microsoft: Deserialization Risks in Use of BinaryFormatter and Related Types](https://learn.microsoft.com/en-us/dotnet/standard/serialization/binaryformatter-security-guide)
- [Python: pickle Security Warning](https://docs.python.org/3/library/pickle.html)
