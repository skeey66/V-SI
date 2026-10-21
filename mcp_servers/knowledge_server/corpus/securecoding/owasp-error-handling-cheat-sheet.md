---
id: owasp-error-handling-cheat-sheet
title: OWASP 오류 처리
source: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Error_Handling_Cheat_Sheet.md
license: CC BY-SA 4.0 (OWASP Cheat Sheet Series)
keys: 
---
## Introduction

Error handling is a part of the overall security of an application. Except in movies, an attack always begins with a **Reconnaissance** phase in which the attacker will try to gather as much technical information (often *name* and *version* properties) as possible about the target, such as the application server, frameworks, libraries, etc.

Unhandled errors can assist an attacker in this initial phase, which is very important for the rest of the attack.

The OWASP Web Security Testing Guide explains [how error messages can expose application details](https://wstg.owasp.org/v4.2/4-Web_Application_Security_Testing/08-Testing_for_Error_Handling/01-Testing_For_Improper_Error_Handling/).

## Context

Issues at the error handling level can reveal a lot of information about the target and can also be used to identify injection points in the target's features.

Below is an example of the disclosure of a technology stack, here the Struts2 and Tomcat versions, via an exception rendered to the user:

```text
HTTP Status 500 - For input string: "null"

type Exception report

message For input string: "null"

description The server encountered an internal error that prevented it from fulfilling this request.

exception

java.lang.NumberFormatException: For input string: "null"
    java.lang.NumberFormatException.forInputString(NumberFormatException.java:65)
    java.lang.Integer.parseInt(Integer.java:492)
    java.lang.Integer.parseInt(Integer.java:527)
    sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
    sun.reflect.NativeMethodAccessorImpl.invoke(NativeMethodAccessorImpl.java:57)
    sun.reflect.DelegatingMethodAccessorImpl.invoke(DelegatingMethodAccessorImpl.java:43)
    java.lang.reflect.Method.invoke(Method.java:606)
    com.opensymphony.xwork2.DefaultActionInvocation.invokeAction(DefaultActionInvocation.java:450)
    com.opensymphony.xwork2.DefaultActionInvocation.invokeActionOnly(DefaultActionInvocation.java:289)
    com.opensymphony.xwork2.DefaultActionInvocation.invoke(DefaultActionInvocation.java:252)
    org.apache.struts2.interceptor.debugging.DebuggingInterceptor.intercept(DebuggingInterceptor.java:256)
    com.opensymphony.xwork2.DefaultActionInvocation.invoke(DefaultActionInvocation.java:246)
    ...

note: The full stack trace of the root cause is available in the Apache Tomcat/7.0.56 logs.
```

Below is an example of disclosure of a SQL query error, along with the site installation path, that can be used to identify an injection point:

```text
Warning: odbc_fetch_array() expects parameter /1 to be resource, boolean given
in D:\app\index_new.php on line 188
```

The [OWASP Testing Guide](https://owasp.org/www-project-web-security-testing-guide/stable/4-Web_Application_Security_Testing/01-Information_Gathering/) provides different techniques to obtain technical information from an application.

## Objective

The article shows how to configure a global error handler as part of your application's runtime configuration. In some cases, it may be more efficient to define this error handler as part of your code. The outcome being that when an unexpected error occurs then a generic response is returned by the application but the error details are logged server side for investigation, and not returned to the user.

The following schema shows the target approach:

![Overview](../assets/Error_Handling_Cheat_Sheet_Overview.png)

As most recent application topologies are *API based*, we assume in this article that the backend exposes only a REST API and does not contain any user interface content. The application should try and exhaustively cover all possible failure modes and use 5xx errors only to indicate responses to requests that it cannot fulfill, but not provide any content as part of the response that would reveal implementation details. For that, [RFC 7807 - Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc7807) defines a document format.  
For the error logging operation itself, the [logging cheat sheet](Logging_Cheat_Sheet.md) should be used. This article focuses on the error handling part.

## Proposition

For each technology stack, the following configuration options are proposed:

## Sources of the prototype

The source code of all the sandbox projects created to find the right setup to use is stored in this [GitHub repository](https://github.com/righettod/poc-error-handling).

## Appendix HTTP Errors

A reference for HTTP errors can be found here [RFC 2616](https://www.ietf.org/rfc/rfc2616.txt). Using error messages that do not provide implementation details is important to avoid information leakage. In general, consider using 4xx error codes for requests that are due to an error on the part of the HTTP client (e.g. unauthorized access, request body too large) and use 5xx to indicate errors that are triggered on server side, due to an unforeseen bug. Ensure that applications are monitored for 5xx errors which are a good indication of the application failing for some sets of inputs.

## References

- [OWASP WSTG: Testing for Improper Error Handling](https://wstg.owasp.org/v4.2/4-Web_Application_Security_Testing/08-Testing_for_Error_Handling/01-Testing_For_Improper_Error_Handling/)
- [RFC 9457: Problem Details for HTTP APIs](https://www.rfc-editor.org/rfc/rfc9457.html)
