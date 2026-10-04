import { UserService } from "../services/user.service";
import { UserModel } from "../models/user.model";

export class UserController {
    constructor(private userService: UserService) {}

    public getUserProfile(userId: string): UserModel | null {
        return this.userService.findUserById(userId);
    }

    public authenticateUser(email: string, pass: string): string {
        return this.userService.login(email, pass);
    }
}

