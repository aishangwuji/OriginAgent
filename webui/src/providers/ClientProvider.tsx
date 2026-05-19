import { createContext, useContext, type ReactNode } from "react";

import type { OpenHomeClient } from "@/lib/OpenHome-client";

interface ClientContextValue {
  client: OpenHomeClient;
  token: string;
  modelName: string | null;
  refreshToken: () => Promise<string | null>;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  refreshToken = async () => null,
  children,
}: {
  client: OpenHomeClient;
  token: string;
  modelName?: string | null;
  refreshToken?: () => Promise<string | null>;
  children: ReactNode;
}) {
  return (
    <ClientContext.Provider value={{ client, token, modelName, refreshToken }}>
      {children}
    </ClientContext.Provider>
  );
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
