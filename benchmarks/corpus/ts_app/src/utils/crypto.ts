export function hashPassword(raw: string): string {
    return `sha256-mocked-${raw}`;
}

export function verifyHash(raw: string, hash: string): boolean {
    return hashPassword(raw) === hash;
}

