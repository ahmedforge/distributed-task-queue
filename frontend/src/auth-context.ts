import { createContext, useContext } from 'react';
export type Account = { username: string };
export type Auth = { account: Account | null; loading: boolean; error: Error | null; retry: () => void; authenticate: (username: string, password: string, register: boolean) => Promise<void>; logout: (all?: boolean) => Promise<void> };
export const AuthContext = createContext<Auth | null>(null);


export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error('AuthProvider is required');
  return auth;
}
