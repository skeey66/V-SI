---
id: owasp-os-command-injection-defense-cheat-sheet
title: OWASP OS 명령 주입 방어
source: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.md
license: CC BY-SA 4.0 (OWASP Cheat Sheet Series)
keys: 
---
## Introduction

Command injection (or OS Command Injection) is a type of injection where software that constructs a system command using externally influenced input does not correctly neutralize the input from special elements that can modify the initially intended command.

For example, if the supplied value is:

``` shell
calc
```

when typed in a Windows command prompt, the application *Calculator* is displayed.

However, if the supplied value has been tampered with, and now it is:

``` shell
calc & echo "test"
```

when executed, it changes the meaning of the initial intended value.

Now, both the *Calculator* application and the value *test* are displayed:

![CommandInjection](../assets/OS_Command_Injection_Defense_Cheat_Sheet_CmdInjection.png)

The problem is exacerbated if the compromised process does not follow the principle of least privileges and attacker-controlled commands end up running with special system privileges that increase the amount of damage.

## Argument Injection

Every OS Command Injection is also an Argument Injection. In this type of attacks, user input can be passed as arguments while executing a specific command.

For example, if the user input is passed through an escape function to escape certain characters like `&`, `|`, `;`, etc.

```php

system("curl " . escape($url));
```

which will prevent an attacker to run other commands.

However, if the attacker controlled string contains an additional argument of the `curl` command:

```php

system("curl " . escape("--help"))
```

Now when the above code is executed, it will show the output of `curl --help`.

Depending upon the system command used, the impact of an Argument injection attack can range from **Information Disclosure** to critical **Remote Code Execution**.

## Defense Option 1: Avoid calling OS commands directly

The primary defense is to avoid calling OS commands directly. Built-in library functions are a very good alternative to OS Commands, as they cannot be manipulated to perform tasks other than those it is intended to do.

For example use `mkdir()` instead of `system("mkdir /dir_name")`.

If there are available libraries or APIs for the language you use, this is the preferred method.

## Defense option 2: Escape values added to OS commands specific to each OS

For examples, see [escapeshellarg()](https://www.php.net/manual/en/function.escapeshellarg.php) in PHP.

The `escapeshellarg()` surrounds the user input in single quotes, so if the malformed user input is something like `& echo "hello"`, the final output will be like `calc '& echo "hello"'` which will be parsed as a single argument to the command `calc`.

Even though `escapeshellarg()` prevents OS Command Injection, an attacker can still pass a single argument to the command.

## Defense option 3: Parameterization in conjunction with Input Validation

If calling a system command that incorporates user-supplied cannot be avoided, the following two layers of defense should be used within software to prevent attacks:

## Layer 1

**Parameterization:** If available, use structured mechanisms that automatically enforce the separation between data and command. These mechanisms can help provide the relevant quoting and encoding.

## Layer 2

**Input validation:** The values for commands and the relevant arguments should be both validated. There are different degrees of validation for the actual command and its arguments:

- When it comes to the **commands** used, these must be validated against a list of allowed commands.
- In regards to the **arguments** used for these commands, they should be validated using the following options:
    - **Positive or allowlist input validation**: Where are the arguments allowed explicitly defined.
    - **Allowlist Regular Expression**: Where a list of good, allowed characters and the maximum length of the string are defined. Ensure that metacharacters like ones specified in `Note A` and whitespaces are not part of the Regular Expression. For example, the following regular expression only allows lowercase letters and numbers and does not contain metacharacters. The length is also being limited to 3-10 characters: `^[a-z0-9]{3,10}$`
- For commands that support it, use `--` to end option parsing. In [curl](https://curl.se/docs/manpage.html#OPTIONS), later arguments are still URL operands: `--` does not prevent additional transfers. Pass exactly one validated URL as one argument using a process API, and disable curl's [URL globbing](https://curl.se/docs/manpage.html#GLOBBING) with `--globoff`. Do not interpolate an unquoted value into a shell command. Argument separation does not validate the destination; apply [SSRF protections](Server_Side_Request_Forgery_Prevention_Cheat_Sheet.md).

**Note A:**

```text
& |  ; $ > < ` \ ! ' " ( )
```

## Additional Defenses

On top of primary defenses, parameterizations, and input validation, we also recommend adopting all of these additional defenses to provide defense in depth.

These additional defenses are:

- Applications should run using the lowest privileges that are required to accomplish the necessary tasks.
- If possible, create isolated accounts with limited privileges that are only used for a single task.

## .NET

- If starting a process is necessary, use a fixed, fully qualified executable path in `FileName` and set [`UseShellExecute = false`](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.useshellexecute#remarks). This starts the executable directly; it does not validate its arguments.
- On modern .NET, add each argument separately with [`ProcessStartInfo.ArgumentList`](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.argumentlist#remarks). It handles escaping, but untrusted input remains a security risk. Validate each value against the called program's allowed options and operands, such as permitted paths or destinations.
- Do not pass untrusted input to command interpreters such as `cmd.exe`, `powershell.exe`, or `sh -c`.
- .NET Framework has no `ArgumentList`. Avoid constructing `Arguments` from untrusted input; if necessary, restrict values to a command-specific allowlist that needs no quoting. [`Arguments` parsing](https://learn.microsoft.com/en-us/dotnet/api/system.diagnostics.processstartinfo.arguments#remarks) depends on the target program and platform.

## References

- [SEI CERT C: Do not call system()](https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/rules/environment-env/env33-c/)
- [CWE-77: Command Injection](https://cwe.mitre.org/data/definitions/77.html)
