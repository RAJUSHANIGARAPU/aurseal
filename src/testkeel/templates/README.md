# Assignment templates

Copier templates rendered by `testkeel new-assignment`, shipped as package data.
One `copier.yml` at this level holds every question; `_subdirectory` routes each pass to
the common skeleton (`_base/`) and then to the type's overlay (`<type>/`).

Templates use no `_tasks` and no Jinja extensions, so rendering never needs `--trust`.
