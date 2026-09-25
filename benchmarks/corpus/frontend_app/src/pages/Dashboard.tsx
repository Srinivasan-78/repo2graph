import { renderHeader } from "../components/Header";
import { renderSidebar } from "../components/Sidebar";
import { renderMetricCard } from "../components/MetricCard";
import { useMetrics } from "../hooks/useMetrics";

export async function renderDashboardPage(): Promise<string> {
    const metrics = await useMetrics();
    const cards = metrics.map(m => renderMetricCard(m)).join("");
    return `<div class="layout">${renderHeader()}${renderSidebar("/dashboard")}<main>${cards}</main></div>`;
}

