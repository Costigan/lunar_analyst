import { describe, expect, it } from "vitest";
import { actionLabel } from "../utils/assistantPolicy";

describe("assistantPolicy", () => {
  it("renders human labels for action types", () => {
    expect(actionLabel("launch_job")).toBe("Launch job");
    expect(actionLabel("import_file")).toBe("Import file");
  });
});
