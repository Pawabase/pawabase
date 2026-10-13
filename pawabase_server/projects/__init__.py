"""Projects: the unit an application lives in, its database binding, and the one place that creates and changes them.

The command line (``pawabase-server project …``) and the runtime agent both call :class:`~pawabase_server.projects.manager.ProjectManager`.
Nothing here is reachable over HTTP from a client of the application.

This package imports nothing eagerly: the environment loader needs :mod:`pawabase_server.projects.refs`, and the manager needs the
environment loader.
"""
