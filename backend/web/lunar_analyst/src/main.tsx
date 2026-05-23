import React from "react";
import ReactDOM from "react-dom/client";
import App from "./AppLayout";
import "./styles/app.css";
import "flexlayout-react/style/combined.css";

// Blueprint CSS
import "@blueprintjs/core/lib/css/blueprint.css";
import "@blueprintjs/icons/lib/css/blueprint-icons.css";
import "@blueprintjs/select/lib/css/blueprint-select.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <App />,
);
