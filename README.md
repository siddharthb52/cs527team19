This project aims to provide an all-in-one tool to evaluate and "lint" code in a more abstract and subjective way than existing tools such as Python Black. It is currently made for Python object oriented projects but is not necessarily limited to them. Although it is given that coding styles and standards vary greatly, this project's goal is to check for universally good practices (or violations thereof). The main axes that this tool evaluates on are as follows:

### Naming
---
Variable, function, and object names should be concise and descriptive. Variable names should be descriptive of the concept that the variable represents moreso than how the variable is used: force should be calculated as `mass * acceleration`, and not `factor1 * factor2`. Function names should, when possible, indicate its role in the program, such as its effect on object state and relation to key variables and objects (note the difference between `calculate()`, `calculateForce()`, and `calculateAndStoreForce()`). Object names should accurately identify a single instance of that object, rather than a collection or the objects' relation to other objects: `Book` vs. `BookList` vs. `LibraryItem`.

Multiple similar variables should follow similar naming conventions. If a physics program is using `F, m, a, g` then velocity should be `v`. Abbreviations should be thoughtfully used, with caution placed towards losing readability or creating ambiguity.

### Object Coupling
---
Objects and classes should avoid depending too heavily on too many other objects. A well-designed object should interact only with the collaborators that are necessary for its responsibility, rather than directly managing or knowing the details of large portions of the system. If changes to one class frequently require changes to several others, or if one object must understand the internal structure of another to do its work, the design is likely too tightly coupled.

Objects should not depend on too many external classes, instantiate concrete dependencies unnecessarily instead of receiving them in a cleaner way, or rely on deep chains of method calls or attribute access. For example, instances of code like `a.getB().getC().doThing()` may indicate that one object knows too much about the internal organization of another.

### Object Cohesion
---
Objects should have clearly defined scopes and boundaries. Functionality should be relevant within an object's scope and intended lifespan. Classes and modules should have one primary responsibility. Methods and attributes within the same object should be meaningfully related. If large groups of fields are only used by small subsets of methods, the object may represent multiple hidden concepts that should be split into separate objects.

### Namespace Pollution
---
Namespaces should remain clean and intentional. Unnecessary imports, broadly exposed names, and generic global identifiers should be avoided. Imports should be intentional and impactful. Imports that are used for one or few function calls should be strictly justified.

### Undocumented Assumptions
---
Assumptions that are required for correctness should be visible in code, especially in complex control flow, nested conditionals, or domain-heavy logic. Key invariants should be documented near where they are introduced and where they are relied upon. If logic assumes that a list is sorted, a value is non-null, a timestamp is UTC, or a state machine is already initialized, that expectation should be explicitly stated. When assumptions cannot be enforced directly, they should still be communicated clearly. Non-trivial and significant preconditions and postconditions should be explicitly mentioned.
