export function renderSidebar(currentPath: string): string {
    const navItems = ["/dashboard", "/analytics", "/settings"];
    const links = navItems.map(item => `<a href="${item}" class="${item === currentPath ? "active" : ""}">${item}</a>`);
    return `<aside><nav>${links.join("")}</nav></aside>`;
}

