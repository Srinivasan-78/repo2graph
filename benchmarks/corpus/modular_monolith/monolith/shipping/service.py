from monolith.kernel.events import global_event_bus


class ShippingService:
    def __init__(self):
        self.shipments = []
        global_event_bus.subscribe("invoice_paid", self.schedule_delivery)

    def schedule_delivery(self, event_data: dict):
        shipment_record = {"order_ref": event_data.get("invoice_id"), "status": "QUEUED"}
        self.shipments.append(shipment_record)
        return shipment_record


shipping_service = ShippingService()
