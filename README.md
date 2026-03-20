This project aims to provide an all-in-one tool to evaluate and "lint" code in a more abstract and subjective way than existing tools such as Python Black. It is currently made for Python object oriented projects but is not necessarily limited to them. Although it is given that coding styles and standards vary greatly, this project's goal is to check for universally good practices (or violations thereof). The main axes that this tool evaluates on are as follows:

### Naming
---
Variable, function, and object names should be concise and descriptive. Variable names should be descriptive of the concept that the variable represents moreso than how the variable is used: force should be calculated as `mass * acceleration`, and not `factor1 * factor2`. Function names should, when possible, indicate its role in the program, such as its effect on object state and relation to key variables and objects (note the difference between `calculate()`, `calculateForce()`, and `calculateAndStoreForce()`). Object names should accurately identify a single instance of that object, rather than a collection or the objects' relation to other objects: `Book` vs. `BookList` vs. `LibraryItem`.

Multiple similar variables should follow similar naming conventions. If a physics program is using `F, m, a, g` then velocity should be `v`. Abbreviations should be thoughtfully used, with caution placed towards losing readability or creating ambiguity.

### Object Coupling
---
Objects and classes should avoid depending too heavily on too many other objects. A well-designed object should interact only with the collaborators that are necessary for its responsibility, rather than directly managing or knowing the details of large portions of the system. High coupling makes code harder to test, modify, and reuse. If changes to one class frequently require changes to several others, or if one object must understand the internal structure of another to do its work, the design is likely too tightly coupled.

This project will evaluate whether objects depend on too many external classes, whether they instantiate concrete dependencies unnecessarily instead of receiving them in a cleaner way, and whether they rely on deep chains of method calls or attribute access. For example, instances of code like `a.getB().getC().doThing()` may indicate that one object knows too much about the internal organization of another.

Another aim of this project with regard to coupling may include checks for patterns such as circular dependencies, classes that serve as overly central hubs, and methods that interact more with foreign objects than with their own state. In general, lower coupling is preferred because it improves modularity, readability, and maintainability.

### Object Cohesion
---

### Namespace Pollution
---
including unnecessary imports and dependencies

### Undocumented Assumptions
---
especially important in large nested ifs or complicated logic
key non-trivial invariants when they are used should be noted, as well as documented with variable declaration
