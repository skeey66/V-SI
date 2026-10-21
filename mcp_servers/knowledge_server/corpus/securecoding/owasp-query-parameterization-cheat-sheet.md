---
id: owasp-query-parameterization-cheat-sheet
title: OWASP 쿼리 파라미터화
source: https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master/cheatsheets/Query_Parameterization_Cheat_Sheet.md
license: CC BY-SA 4.0 (OWASP Cheat Sheet Series)
keys: 
---
## Introduction

[SQL Injection](https://owasp.org/www-community/attacks/SQL_Injection) is one of the most dangerous web vulnerabilities. As of the [OWASP Top 10:2025](https://owasp.org/Top10/2025/A05_2025-Injection/), it is categorized under A05:2025-Injection.

It represents a serious threat because SQL Injection allows evil attacker code to change the structure of a web application's SQL statement in a way that can steal data, modify data, or potentially facilitate command injection to the underlying OS.

This cheat sheet is a derivative work of the [SQL Injection Prevention Cheat Sheet](SQL_Injection_Prevention_Cheat_Sheet.md).

## Parameterized Query Examples

SQL Injection is best prevented through the use of [*parameterized queries*](SQL_Injection_Prevention_Cheat_Sheet.md). The following chart demonstrates, with real-world code samples, how to build parameterized queries in most of the common web languages. The purpose of these code samples is to demonstrate to the web developer how to avoid SQL Injection when building database queries within a web application.

Please note, many client side frameworks and libraries offer client side query parameterization. These libraries often just build queries with string concatenation before sending raw queries to a server. Please ensure that query parameterization is done server-side!

## Using .NET built-in feature

```csharp
String query = "SELECT account_balance FROM user_data WHERE user_name = ?";
try {
   OleDbCommand command = new OleDbCommand(query, connection);
   command.Parameters.Add(new OleDbParameter("customerName", CustomerName.Text));
   OleDbDataReader reader = command.ExecuteReader();
   // …
} catch (OleDbException se) {
   // error handling
}
```

## Create

Project.create!(:name => 'owasp')

## Read

Project.all(:conditions => "name = ?", name)
Project.all(:conditions => { :name => name })
Project.where("name = :name", :name => name)

## Update

project.update_attributes(:name => 'owasp')

## Delete

Project.delete(:name => 'name')
```

## Using Cold Fusion built-in feature

```coldfusion
<cfquery name = "getFirst" dataSource = "cfsnippets">
    SELECT * FROM #strDatabasePrefix#_courses WHERE intCourseID =
    <cfqueryparam value = #intCourseID# CFSQLType = "CF_SQL_INTEGER">
</cfquery>
```

## Stored Procedure Examples

The SQL you write in your web application isn't the only place that SQL injection vulnerabilities can be introduced. If you are using Stored Procedures, and you are dynamically constructing SQL inside them, you can also introduce SQL injection vulnerabilities.

Dynamic SQL can be parameterized using bind variables, to ensure the dynamically constructed SQL is secure.

Here are some examples of using bind variables in stored procedures in different databases.

## References

- [Oracle JDBC: Using Prepared Statements](https://docs.oracle.com/javase/tutorial/jdbc/basics/prepared.html)
- [PHP PDO: Prepared Statements and Stored Procedures](https://www.php.net/pdo.prepared-statements)
