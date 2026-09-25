import { renderMetricCard } from "../components/MetricCard";
import { MetricItem } from "../types/dashboard";

describe("MetricCardComponent", () => {
    it("renders metric label and formatted value", () => {
        const sample: MetricItem = {
            id: "m-test",
            label: "Total Revenue",
            value: 99000,
            trend: "up"
        };
        const html = renderMetricCard(sample);
        expect(html).toContain("Total Revenue");
        expect(html).toContain("99000 (up)");
    });
});

