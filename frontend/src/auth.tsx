import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!getToken());

  useEffect(() => {
    if (getToken()) {
      api<User>("/auth/me")
        .then(setUser)
        .catch(() => setToken(null))
        .finally(() => setLoading(false));
    }
    const onLogout = () => setUser(null);
    window.addEventListener("tasmac-logout", onLogout);
    return () => window.removeEventListener("tasmac-logout", onLogout);
  }, []);

  const login = async (email: string, password: string) => {
    const r = await api<{ access_token: string; user: User }>("/auth/login", "POST", { email, password });
    setToken(r.access_token);
    setUser(r.user);
  };
  const logout = () => {
    setToken(null);
    setUser(null);
  };
  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}
