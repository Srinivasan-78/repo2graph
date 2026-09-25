import { renderHeader } from "../components/Header";
import { renderSidebar } from "../components/Sidebar";
import { renderDataTable } from "../components/DataTable";
import { useMetrics } from "../hooks/useMetrics";

export async function renderAnalyticsPage(): Promise<string> {
    const metrics = await useMetrics();
    return `<div class="layout">${renderHeader()}${renderSidebar("/analytics")}<main>${renderDataTable(metrics)}</main></div>`;
}

