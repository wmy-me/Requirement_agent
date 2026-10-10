import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import "./styles/index.css";
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15_000 } } });
createRoot(document.getElementById("root")!).render(<React.StrictMode><BrowserRouter basename="/app-next"><QueryClientProvider client={queryClient}><App /></QueryClientProvider></BrowserRouter></React.StrictMode>);
