#[test]
fn folder_scans_leave_the_window_responsive_and_coalesce_requests() {
    in_own_process("folder_scan_window");
}

#[gpui::test]
fn folder_scan_window(cx: &mut gpui::TestAppContext) {
    if !running_alone("folder_scan_window") {
        return;
    }
    let (view, cx) = open_window(cx, "scan-fixture", "", serde_json::json!({
        "id": "root", "type": "row", "children": []
    }));
    cx.run_until_parked();
    view.update_in(cx, |this, window, cx| {
        this.folders.remove("scan-fixture");
        this.sync_folders(window, cx);
        assert!(this.folder_scan.is_some());
        assert!(!this.folders.contains_key("scan-fixture"), "disk work is deferred");
        assert!(this.control(serde_json::json!({"status": true}), window, cx).get("error").is_none());
        assert!(!this.folder_rescan);
        this.sync_folders(window, cx);
        this.sync_folders(window, cx);
        assert!(this.folder_rescan, "requests during a scan are retained");
    });
    cx.run_until_parked();
    view.update(cx, |this, _| {
        assert!(this.folders.contains_key("scan-fixture"));
        assert!(this.folder_scan.is_none());
        assert!(!this.folder_rescan, "the follow-up scan completed");
    });
}
