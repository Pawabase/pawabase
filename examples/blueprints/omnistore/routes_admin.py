"""Back-office routes: what the dashboard and the stock room call.

Policies carry the weight here — a shopper's token reaches none of these — and
the routes stay thin: name the input schema, name the flow, and let the flow
explain what it does in its own description.
"""

from __future__ import annotations

from helpers import route

MERCHANT = ["Merchant"]
CATALOG = ["Catalog", "Merchant"]
INV = ["Inventory", "Merchant"]
ORDERS = ["Orders", "Merchant"]
CONTENT = ["Content", "Moderation"]
SUPPORT = ["Support"]

ROUTES = [
    route("GET", "/merchant/dashboard", "store_dashboard", "Headline numbers for the active store.",
          "store_staff", "store_dashboard", tags=MERCHANT, cache=30),
    route("GET", "/merchant/orders/{id}", "order_detail", "One order with lines, money, parcels, returns and timeline.",
          "store_staff", "order_detail", tags=ORDERS, cache=15),
    route("POST", "/catalog/products/{id}/publish", "product_publish", "Publish a product, if it is fit to sell.",
          "store_editor", "product_publish", tags=CATALOG),
    route("POST", "/content/reviews/{id}/moderate", "moderate_review", "Approve or reject a review and re-score the product.",
          "moderator", "moderate_review", tags=CONTENT, input_schema="ReviewModeration"),
    route("POST", "/support/tickets/{id}/reply", "staff_reply", "Answer a ticket (an internal note stays internal).",
          "support_desk", "staff_reply", tags=SUPPORT, input_schema="TicketReply", rate={"limit": 60, "window": 60}),
    route("POST", "/inventory/adjust", "stock_adjust", "Move one shelf's count, with a reason.",
          "inventory_staff", "stock_adjust", tags=INV, input_schema="StockAdjust"),
    route("POST", "/purchase-orders/{id}/receive", "receive_purchase_order", "Land the goods on a purchase order.",
          "inventory_staff", "receive_purchase_order", tags=INV),
    route("POST", "/inventory/transfers", "transfer_stock", "Move stock between two warehouses atomically.",
          "inventory_staff", "transfer_stock", tags=INV, input_schema="StockTransfer"),
    route("POST", "/inventory/counts/{id}/post", "post_stock_count", "Reconcile a physical count and journal the variance.",
          "inventory_staff", "post_stock_count", tags=INV),
    route("GET", "/inventory/low", "low_stock_view", "Every active variant at or under its reorder point.",
          "store_staff", "low_stock_view", tags=INV, cache=30),
    route("POST", "/orders/{id}/mark-paid", "mark_paid", "Record an offline payment against an order.",
          "fulfilment_staff", "mark_paid", tags=ORDERS, input_schema="PaymentCapture"),
    route("POST", "/orders/{id}/fulfil", "fulfil_order", "Ship a paid order and tell the customer.",
          "fulfilment_staff", "fulfil_order", tags=ORDERS, input_schema="ShipmentInput"),
    route("POST", "/orders/{id}/cancel", "cancel_order", "Cancel an unpaid, unshipped order and release its holds.",
          "store_manager", "cancel_order", tags=ORDERS, input_schema="OrderCancel"),
    route("POST", "/orders/{id}/refunds", "refund_order", "Refund a paid order, restocking it if the goods are coming back.",
          "store_manager", "refund_order", tags=ORDERS, input_schema="RefundInput"),
    route("POST", "/returns/{id}/decision", "decide_return", "Approve or reject a return; approval puts the stock back.",
          "fulfilment_staff", "decide_return", tags=ORDERS, input_schema="ReturnDecision"),
]
