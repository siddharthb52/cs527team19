This project aims to provide an all-in-one tool to evaluate and "lint" code in a more abstract and subjective way than existing tools such as Python Black. It is currently made for Python object oriented projects but is not necessarily limited to them. Although it is given that coding styles and standards vary greatly, this project's goal is to check for universally good practices (or violations thereof). The main axes that this tool evaluates on are as follows:

### Naming
---
Variable, function, and object names should be concise and descriptive. Variable names should be descriptive of the concept that the variable represents moreso than how the variable is used: force should be calculated as `mass * acceleration`, and not `factor1 * factor2`. Function names should, when possible, indicate its role in the program, such as its effect on object state and relation to key variables and objects (note the difference between `calculate()`, `calculateForce()`, and `calculateAndStoreForce()`). Object names should accurately identify a single instance of that object, rather than a collection or the objects' relation to other objects: `Book` vs. `BookList` vs. `LibraryItem`.

Multiple similar variables should follow similar naming conventions. If a physics program is using `F, m, a, g` then velocity should be `v`. Abbreviations should be thoughtfully used, with caution placed towards losing readability or creating ambiguity.

### Object Coupling
---

### Object Cohesion
---

### Namespace Pollution
---
including unnecessary imports and dependencies

### Undocumented Assumptions
---
especially important in large nested ifs or complicated logic
key non-trivial invariants when they are used should be noted, as well as documented with variable declaration