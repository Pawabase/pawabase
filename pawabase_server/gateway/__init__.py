"""The gateway fleet: what makes one gateway replica visible to Studio and to a load balancer's health check.

Nothing here is in the request path of an application request. A replica serves traffic identically whether or not it is registered;
the registry is observability and fleet management (Studio's instance list, §2 of ``docs/gateway-replication-design.md``), never a
precondition for the gateway to answer a request.
"""
