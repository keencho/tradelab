#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{
    image::Image,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const WIDGET_LABEL: &str = "widget";
const SETTINGS_LABEL: &str = "settings";

fn toggle_widget(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(WIDGET_LABEL) {
        match win.is_visible() {
            Ok(true) => {
                let _ = win.hide();
            }
            _ => {
                let _ = win.show();
                let _ = win.set_focus();
            }
        }
    }
}

fn open_settings(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(SETTINGS_LABEL) {
        let _ = win.show();
        let _ = win.set_focus();
        return;
    }
    let _ = WebviewWindowBuilder::new(app, SETTINGS_LABEL, WebviewUrl::App("settings.html".into()))
        .title("설정")
        .inner_size(360.0, 460.0)
        .resizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .build();
}

fn quit_app(app: &AppHandle) {
    app.exit(0);
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_http::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    // Ctrl+Shift+X: toggle widget
                    if shortcut.matches(Modifiers::CONTROL | Modifiers::SHIFT, Code::KeyX) {
                        toggle_widget(app);
                    }
                    // Ctrl+Shift+H: panic-hide (boss key)
                    if shortcut.matches(Modifiers::CONTROL | Modifiers::SHIFT, Code::KeyH) {
                        if let Some(win) = app.get_webview_window(WIDGET_LABEL) {
                            let _ = win.hide();
                        }
                    }
                })
                .build(),
        )
        .setup(|app| {
            let handle = app.handle().clone();

            // global shortcuts
            let toggle_sc = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyX);
            let hide_sc = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyH);
            let _ = app.global_shortcut().register(toggle_sc);
            let _ = app.global_shortcut().register(hide_sc);

            // tray
            let icon = app
                .default_window_icon()
                .cloned()
                .unwrap_or_else(|| Image::from_bytes(include_bytes!("../icons/icon.png")).unwrap());

            let show_item = MenuItem::with_id(app, "show", "보이기/숨기기 (Ctrl+Shift+X)", true, None::<&str>)?;
            let settings_item = MenuItem::with_id(app, "settings", "설정", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "종료", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &settings_item, &quit_item])?;

            let _tray = TrayIconBuilder::with_id("main")
                .icon(icon)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .tooltip("TradeLab")
                .on_menu_event({
                    let h = handle.clone();
                    move |_app, ev| match ev.id().as_ref() {
                        "show" => toggle_widget(&h),
                        "settings" => open_settings(&h),
                        "quit" => quit_app(&h),
                        _ => {}
                    }
                })
                .on_tray_icon_event({
                    let h = handle.clone();
                    move |_tray, ev| {
                        if let TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        } = ev
                        {
                            toggle_widget(&h);
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
