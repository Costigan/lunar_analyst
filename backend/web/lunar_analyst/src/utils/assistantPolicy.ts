import type { AssistantActionType } from "../services/assistantService";

export function actionLabel(action: AssistantActionType): string {
  switch (action) {
    case "launch_job":
      return "Launch job";
    case "import_file":
      return "Import file";
    case "move_path":
      return "Move path";
    case "update_layer_state":
      return "Update layer";
    case "delete_artifact":
      return "Delete artifact";
    case "write_notebook":
      return "Write notebook";
    default:
      return action;
  }
}
