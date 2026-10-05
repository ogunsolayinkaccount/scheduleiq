import React from "react";
import ReactDOM from "react-dom/client";
import App from "../App";
import { AuthProvider } from "../AuthContext";
import { installCsrfFetchWrapper } from "../auth";
import "./index.css";

// Phase 3 (Authentication and Authorization) — must install before any
// component's first fetch() call, so every mutating request from anywhere
// in the app carries the CSRF header from the start.
installCsrfFetchWrapper();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>
);
