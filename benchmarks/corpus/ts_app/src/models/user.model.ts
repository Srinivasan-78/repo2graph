export interface UserModel {
    id: string;
    email: string;
    passwordHash: string;
    role: "admin" | "user" | "guest";
    active: boolean;
}

