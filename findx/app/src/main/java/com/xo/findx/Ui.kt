package com.xo.findx

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.text.DateFormat
import java.util.Date

// ---------------------------------------------------------------- icons

/** A few icons (Material shapes), drawn here so the app doesn't carry the whole icon library. */
object FxIcons {
    private fun filled(name: String, d: String) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)
        .addPath(PathParser().parsePathString(d).toNodes(), fill = SolidColor(Color.White)).build()

    private fun stroked(name: String, vararg ds: String) = ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
        ds.forEach {
            addPath(PathParser().parsePathString(it).toNodes(), stroke = SolidColor(Color.White), strokeLineWidth = 1.7f,
                strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)
        }
    }.build()

    val Photo = filled("photo", "M21,19V5c0,-1.1 -0.9,-2 -2,-2H5c-1.1,0 -2,0.9 -2,2v14c0,1.1 0.9,2 2,2h14c1.1,0 2,-0.9 2,-2zM8.5,13.5l2.5,3.01L14.5,12l4.5,6H5l3.5,-4.5z")
    val Folder = filled("folder", "M10,4H4c-1.1,0 -1.99,0.9 -1.99,2L2,18c0,1.1 0.9,2 2,2h16c1.1,0 2,-0.9 2,-2V8c0,-1.1 -0.9,-2 -2,-2h-8l-2,-2z")
    val Vault = filled("vault", "M12,1L3,5v6c0,5.55 3.84,10.74 9,12c5.16,-1.26 9,-6.45 9,-12V5L12,1zM12,7c1.4,0 2.5,1.1 2.5,2.5V11h0.5c0.55,0 1,0.45 1,1v4c0,0.55 -0.45,1 -1,1H9c-0.55,0 -1,-0.45 -1,-1v-4c0,-0.55 0.45,-1 1,-1h0.5V9.5C9.5,8.1 10.6,7 12,7zM12,8.4c-0.6,0 -1.1,0.5 -1.1,1.1V11h2.2V9.5c0,-0.6 -0.5,-1.1 -1.1,-1.1z")
    val Backspace = filled("backspace", "M22,3H7c-0.69,0 -1.23,0.35 -1.59,0.88L0,12l5.41,8.11c0.36,0.53 0.9,0.89 1.59,0.89h15c1.1,0 2,-0.9 2,-2V5c0,-1.1 -0.9,-2 -2,-2zM19,15.59L17.59,17 14,13.41 10.41,17 9,15.59 12.59,12 9,8.41 10.41,7 14,10.59 17.59,7 19,8.41 15.41,12 19,15.59z")
    val Save = filled("save", "M19,9h-4V3H9v6H5l7,7 7,-7zM5,18v2h14v-2H5z")
    val Move = filled("move", "M20,6h-8l-2,-2H4c-1.1,0 -1.99,0.9 -1.99,2L2,18c0,1.1 0.9,2 2,2h16c1.1,0 2,-0.9 2,-2V8c0,-1.1 -0.9,-2 -2,-2zM12,17v-3H8v-4h4V7l5,5 -5,5z")
    val Share = filled("share", "M18,16.08c-0.76,0 -1.44,0.3 -1.96,0.77L8.91,12.7c0.05,-0.23 0.09,-0.46 0.09,-0.7s-0.04,-0.47 -0.09,-0.7l7.05,-4.11c0.54,0.5 1.25,0.81 2.04,0.81 1.66,0 3,-1.34 3,-3s-1.34,-3 -3,-3 -3,1.34 -3,3c0,0.24 0.04,0.47 0.09,0.7L8.04,9.81C7.5,9.31 6.79,9 6,9c-1.66,0 -3,1.34 -3,3s1.34,3 3,3c0.79,0 1.5,-0.31 2.04,-0.81l7.12,4.16c-0.05,0.21 -0.08,0.43 -0.08,0.65 0,1.61 1.31,2.92 2.92,2.92 1.61,0 2.92,-1.31 2.92,-2.92s-1.31,-2.92 -2.92,-2.92z")
    val Trash = filled("trash", "M6,19c0,1.1 0.9,2 2,2h8c1.1,0 2,-0.9 2,-2V7H6v12zM19,4h-3.5l-1,-1h-5l-1,1H5v2h14V4z")
    val Info = filled("info", "M12,2C6.48,2 2,6.48 2,12s4.48,10 10,10 10,-4.48 10,-10S17.52,2 12,2zM13,17h-2v-6h2v6zM13,9h-2V7h2v2z")
    val Plus = filled("plus", "M19,13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z")
    val Lock = filled("lock", "M18,8h-1V6c0,-2.76 -2.24,-5 -5,-5S7,3.24 7,6v2H6c-1.1,0 -2,0.9 -2,2v10c0,1.1 0.9,2 2,2h12c1.1,0 2,-0.9 2,-2V10c0,-1.1 -0.9,-2 -2,-2zM12,17c-1.1,0 -2,-0.9 -2,-2s0.9,-2 2,-2 2,0.9 2,2 -0.9,2 -2,2zM15.1,8H8.9V6c0,-1.71 1.39,-3.1 3.1,-3.1 1.71,0 3.1,1.39 3.1,3.1v2z")
    val Gear = filled("gear", "M19.14,12.94c0.04,-0.3 0.06,-0.61 0.06,-0.94c0,-0.32 -0.02,-0.64 -0.07,-0.94l2.03,-1.58c0.18,-0.14 0.23,-0.41 0.12,-0.61l-1.92,-3.32c-0.12,-0.22 -0.37,-0.29 -0.59,-0.22l-2.39,0.96c-0.5,-0.38 -1.03,-0.7 -1.62,-0.94L14.4,2.81c-0.04,-0.24 -0.24,-0.41 -0.48,-0.41h-3.84c-0.24,0 -0.43,0.17 -0.47,0.41L9.25,5.35C8.66,5.59 8.12,5.92 7.63,6.29L5.24,5.33c-0.22,-0.08 -0.47,0 -0.59,0.22L2.74,8.87C2.62,9.08 2.66,9.34 2.86,9.48l2.03,1.58C4.84,11.36 4.8,11.69 4.8,12s0.02,0.64 0.07,0.94l-2.03,1.58c-0.18,0.14 -0.23,0.41 -0.12,0.61l1.92,3.32c0.12,0.22 0.37,0.29 0.59,0.22l2.39,-0.96c0.5,0.38 1.03,0.7 1.62,0.94l0.36,2.54c0.05,0.24 0.24,0.41 0.48,0.41h3.84c0.24,0 0.44,-0.17 0.47,-0.41l0.36,-2.54c0.59,-0.24 1.13,-0.56 1.62,-0.94l2.39,0.96c0.22,0.08 0.47,0 0.59,-0.22l1.92,-3.32c0.12,-0.22 0.07,-0.47 -0.12,-0.61L19.14,12.94zM12,15.6c-1.98,0 -3.6,-1.62 -3.6,-3.6s1.62,-3.6 3.6,-3.6s3.6,1.62 3.6,3.6S13.98,15.6 12,15.6z")
    val Back = filled("back", "M20,11H7.83l5.59,-5.59L12,4l-8,8 8,8 1.41,-1.41L7.83,13H20v-2z")
    val Close = filled("close", "M19,6.41L17.59,5 12,10.59 6.41,5 5,6.41 10.59,12 5,17.59 6.41,19 12,13.41 17.59,19 19,17.59 13.41,12z")
    val More = filled("more", "M12,8c1.1,0 2,-0.9 2,-2s-0.9,-2 -2,-2 -2,0.9 -2,2 0.9,2 2,2zM12,10c-1.1,0 -2,0.9 -2,2s0.9,2 2,2 2,-0.9 2,-2 -0.9,-2 -2,-2zM12,16c-1.1,0 -2,0.9 -2,2s0.9,2 2,2 2,-0.9 2,-2 -0.9,-2 -2,-2z")
    val Cloud = filled("cloud", "M19.35,10.04C18.67,6.59 15.64,4 12,4 9.11,4 6.6,5.64 5.35,8.04 2.34,8.36 0,10.91 0,14c0,3.31 2.69,6 6,6h13c2.76,0 5,-2.24 5,-5 0,-2.64 -2.05,-4.78 -4.65,-4.96z")
    val CloudDone = filled("cloud-done", "M19.35,10.04C18.67,6.59 15.64,4 12,4 9.11,4 6.6,5.64 5.35,8.04 2.34,8.36 0,10.91 0,14c0,3.31 2.69,6 6,6h13c2.76,0 5,-2.24 5,-5 0,-2.64 -2.05,-4.78 -4.65,-4.96zM10,17l-3.5,-3.5 1.41,-1.41L10,14.17 15.18,9l1.41,1.41L10,17z")
    val CloudUp = filled("cloud-up", "M19.35,10.04C18.67,6.59 15.64,4 12,4 9.11,4 6.6,5.64 5.35,8.04 2.34,8.36 0,10.91 0,14c0,3.31 2.69,6 6,6h13c2.76,0 5,-2.24 5,-5 0,-2.64 -2.05,-4.78 -4.65,-4.96zM14,13v4h-4v-4H7l5,-5 5,5h-3z")
    val CloudDown = filled("cloud-down", "M19.35,10.04C18.67,6.59 15.64,4 12,4 9.11,4 6.6,5.64 5.35,8.04 2.34,8.36 0,10.91 0,14c0,3.31 2.69,6 6,6h13c2.76,0 5,-2.24 5,-5 0,-2.64 -2.05,-4.78 -4.65,-4.96zM17,13l-5,5 -5,-5h3V9h4v4h3z")
    val Check = filled("check", "M9,16.17L4.83,12l-1.42,1.41L9,19 21,7l-1.41,-1.41z")
    val Play = filled("play", "M8,5v14l11,-7z")
    val Heart = filled("heart", "M12,21.35l-1.45,-1.32C5.4,15.36 2,12.28 2,8.5 2,5.42 4.42,3 7.5,3c1.74,0 3.41,0.81 4.5,2.09C13.09,3.81 14.76,3 16.5,3 19.58,3 22,5.42 22,8.5c0,3.78 -3.4,6.86 -8.55,11.54L12,21.35z")
    val HeartOutline = filled("heart-outline", "M16.5,3c-1.74,0 -3.41,0.81 -4.5,2.09C10.91,3.81 9.24,3 7.5,3 4.42,3 2,5.42 2,8.5c0,3.78 3.4,6.86 8.55,11.54L12,21.35l1.45,-1.32C18.6,15.36 22,12.28 22,8.5 22,5.42 19.58,3 16.5,3zM12.1,18.55l-0.1,0.1 -0.1,-0.1C7.14,14.24 4,11.39 4,8.5 4,6.5 5.5,5 7.5,5c1.54,0 3.04,0.99 3.57,2.36h1.87C13.46,5.99 14.96,5 16.5,5c2,0 3.5,1.5 3.5,3.5 0,2.89 -3.14,5.74 -7.9,10.05z")
    val Restore = filled("restore", "M13,3c-4.97,0 -9,4.03 -9,9H1l3.89,3.89 0.07,0.14L9,12H6c0,-3.87 3.13,-7 7,-7s7,3.13 7,7 -3.13,7 -7,7c-1.93,0 -3.68,-0.79 -4.94,-2.06l-1.42,1.42C8.27,19.99 10.51,21 13,21c4.97,0 9,-4.03 9,-9s-4.03,-9 -9,-9zM12,8v5l4.28,2.54 0.72,-1.21 -3.5,-2.08V8H12z")
    val Slideshow = filled("slideshow", "M10,8v8l5,-4 -5,-4zM19,3H5c-1.1,0 -2,0.9 -2,2v14c0,1.1 0.9,2 2,2h14c1.1,0 2,-0.9 2,-2V5c0,-1.1 -0.9,-2 -2,-2zM19,19H5V5h14v14z")
    val Video = filled("video", "M17,10.5V7c0,-0.55 -0.45,-1 -1,-1H4c-0.55,0 -1,0.45 -1,1v10c0,0.55 0.45,1 1,1h12c0.55,0 1,-0.45 1,-1v-3.5l4,4v-11l-4,4z")
    val Search = filled("search", "M15.5,14h-0.79l-0.28,-0.27C15.41,12.59 16,11.11 16,9.5 16,5.91 13.09,3 9.5,3S3,5.91 3,9.5 5.91,16 9.5,16c1.61,0 3.09,-0.59 4.23,-1.57l0.27,0.28v0.79l5,4.99L20.49,19l-4.99,-5zM9.5,14C7.01,14 5,11.99 5,9.5S7.01,5 9.5,5 14,7.01 14,9.5 11.99,14 9.5,14z")
    val OpenLink = filled("open-link", "M19,19H5V5h7V3H5c-1.11,0 -2,0.9 -2,2v14c0,1.1 0.89,2 2,2h14c1.1,0 2,-0.9 2,-2v-7h-2v7zM14,3v2h3.59l-9.83,9.83 1.41,1.41L19,6.41V10h2V3h-7z")
    val AlbumAdd = filled("album-add", "M4,6H2v14c0,1.1 0.9,2 2,2h14v-2H4V6zM20,2H8c-1.1,0 -2,0.9 -2,2v12c0,1.1 0.9,2 2,2h12c1.1,0 2,-0.9 2,-2V4c0,-1.1 -0.9,-2 -2,-2zM19,11h-4v4h-2v-4H9V9h4V5h2v4h4v2z")
    val Copies = filled("copies", "M16,1H4c-1.1,0 -2,0.9 -2,2v14h2V3h12V1zM19,5H8c-1.1,0 -2,0.9 -2,2v14c0,1.1 0.9,2 2,2h11c1.1,0 2,-0.9 2,-2V7c0,-1.1 -0.9,-2 -2,-2zM19,21H8V7h11v14z")
    val Chevron = filled("chevron", "M10,6L8.59,7.41 13.17,12l-4.58,4.59L10,18l6,-6z")
    val Pin = filled("pin", "M12,2C8.13,2 5,5.13 5,9c0,5.25 7,13 7,13s7,-7.75 7,-13c0,-3.87 -3.13,-7 -7,-7zM12,11.5c-1.38,0 -2.5,-1.12 -2.5,-2.5s1.12,-2.5 2.5,-2.5 2.5,1.12 2.5,2.5 -1.12,2.5 -2.5,2.5z")
    val Radar = stroked("radar", "M12,12m-9,0a9,9 0 1,0 18,0a9,9 0 1,0 -18,0", "M12,12m-5,0a5,5 0 1,0 10,0a5,5 0 1,0 -10,0", "M12,12L18.5,5.5")
    val Bell = filled("bell", "M12,22c1.1,0 2,-0.9 2,-2h-4c0,1.1 0.89,2 2,2zM18,16v-5c0,-3.07 -1.64,-5.64 -4.5,-6.32V4c0,-0.83 -0.67,-1.5 -1.5,-1.5s-1.5,0.67 -1.5,1.5v0.68C7.63,5.36 6,7.92 6,11v5l-2,2v1h16v-1l-2,-2z")
    val Map = filled("map", "M20.5,3l-0.16,0.03L15,5.1 9,3 3.36,4.9c-0.21,0.07 -0.36,0.25 -0.36,0.48V20.5c0,0.28 0.22,0.5 0.5,0.5l0.16,-0.03L9,18.9l6,2.1 5.64,-1.9c0.21,-0.07 0.36,-0.25 0.36,-0.48V3.5c0,-0.28 -0.22,-0.5 -0.5,-0.5zM15,19l-6,-2.11V5l6,2.11V19z")
    val Phone = filled("phone", "M16,1H8C6.34,1 5,2.34 5,4v16c0,1.66 1.34,3 3,3h8c1.66,0 3,-1.34 3,-3V4c0,-1.66 -1.34,-3 -3,-3zM14,21h-4v-1h4v1zM17.25,18H6.75V4h10.5v14z")
    val Devices = filled("devices", "M4,6h18V4H4c-1.1,0 -2,0.9 -2,2v11H0v3h14v-3H4V6zM23,8h-6c-0.55,0 -1,0.45 -1,1v10c0,0.55 0.45,1 1,1h6c0.55,0 1,-0.45 1,-1V9c0,-0.55 -0.45,-1 -1,-1zM22,17h-4v-7h4v7z")
    val Target = stroked("target", "M12,12m-8,0a8,8 0 1,0 16,0a8,8 0 1,0 -16,0", "M12,2v4", "M12,18v4", "M2,12h4", "M18,12h4", "M12,12m-1.5,0a1.5,1.5 0 1,0 3,0a1.5,1.5 0 1,0 -3,0")
    val Arrow = filled("arrow", "M12,2L4.5,20.29l0.71,0.71L12,18l6.79,3 0.71,-0.71z")
    val Battery = filled("battery", "M15.67,4H14V2h-4v2H8.33C7.6,4 7,4.6 7,5.33v15.33C7,21.4 7.6,22 8.33,22h7.33c0.74,0 1.34,-0.6 1.34,-1.33V5.33C17,4.6 16.4,4 15.67,4z")
    val Link = filled("link", "M3.9,12c0,-1.71 1.39,-3.1 3.1,-3.1h4V7H7c-2.76,0 -5,2.24 -5,5s2.24,5 5,5h4v-1.9H7c-1.71,0 -3.1,-1.39 -3.1,-3.1zM8,13h8v-2H8v2zM17,7h-4v1.9h4c1.71,0 3.1,1.39 3.1,3.1s-1.39,3.1 -3.1,3.1h-4V17h4c2.76,0 5,-2.24 5,-5s-2.24,-5 -5,-5z")
    val Bolt = filled("bolt", "M11,21h-1l1,-7H7.5c-0.58,0 -0.57,-0.32 -0.38,-0.66 0.19,-0.34 0.05,-0.08 0.07,-0.12C8.48,10.94 10.42,7.54 13,3h1l-1,7h3.5c0.49,0 0.56,0.33 0.47,0.51l-0.07,0.15C12.96,17.55 11,21 11,21z")
    val Flag = filled("flag", "M14.4,6L14,4H5v17h2v-7h5.6l0.4,2h7V6z")
    val Spot = stroked("spot", "M12,12m-3,0a3,3 0 1,0 6,0a3,3 0 1,0 -6,0", "M12,12m-7,0a7,7 0 1,0 14,0a7,7 0 1,0 -14,0")
    val People = filled("people", "M16,11c1.66,0 2.99,-1.34 2.99,-3S17.66,5 16,5c-1.66,0 -3,1.34 -3,3s1.34,3 3,3zM8,11c1.66,0 2.99,-1.34 2.99,-3S9.66,5 8,5C6.34,5 5,6.34 5,8s1.34,3 3,3zM8,13c-2.33,0 -7,1.17 -7,3.5V19h14v-2.5c0,-2.33 -4.67,-3.5 -7,-3.5zM16,13c-0.29,0 -0.62,0.02 -0.97,0.05 1.16,0.84 1.97,1.97 1.97,3.45V19h6v-2.5c0,-2.33 -4.67,-3.5 -7,-3.5z")
    val Copy = filled("copy", "M16,1H4c-1.1,0 -2,0.9 -2,2v14h2V3h12V1zM19,5H8c-1.1,0 -2,0.9 -2,2v14c0,1.1 0.9,2 2,2h11c1.1,0 2,-0.9 2,-2V7c0,-1.1 -0.9,-2 -2,-2zM19,21H8V7h11v14z")
    val Fingerprint = stroked(
        "fingerprint",
        "M5.5,8.5c1.4,-2.3 3.8,-3.7 6.5,-3.7s5.1,1.4 6.5,3.7",
        "M4.5,13.5c0,-4.1 3.4,-7.4 7.5,-7.4s7.5,3.3 7.5,7.4",
        "M7.5,18.5c-0.6,-1.5 -0.9,-3.1 -0.9,-4.8c0,-3 2.4,-5.4 5.4,-5.4s5.4,2.4 5.4,5.4c0,0.8 0,1.6 -0.1,2.4",
        "M10,20.5c-0.6,-1.9 -1,-4 -1,-6.4c0,-1.7 1.3,-3 3,-3s3,1.3 3,3c0,2.6 -0.4,4.6 -1.1,6.6",
        "M12,14.1c0,2.6 0.4,4.8 1,6.6",
    )
}

// ---------------------------------------------------------------- text and layout

@Composable
fun Title(text: String) = Text(text, color = White, fontSize = 30.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp)

@Composable
fun Body(text: String, color: Color = Grey2) = Text(text, color = color, fontSize = 15.sp, lineHeight = 21.sp)

@Composable
fun Section(title: String) {
    Spacer(Modifier.height(24.dp))
    Text(title, color = Grey, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 4.dp))
    Spacer(Modifier.height(8.dp))
}

/** One UI style big title with actions on the right and an optional count/subtitle under it. */
@Composable
fun LargeHeader(title: String, subtitle: String? = null, onBack: (() -> Unit)? = null, actions: @Composable RowScope.() -> Unit = {}) {
    Column(Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (onBack != null) IconAction(FxIcons.Back, "Back", onBack)
            Spacer(Modifier.weight(1f))
            actions()
        }
        Text(title, color = White, fontSize = 32.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.6).sp,
            maxLines = 1, modifier = Modifier.padding(start = 12.dp, top = 6.dp))
        if (subtitle != null) Text(subtitle, color = Grey, fontSize = 14.sp, modifier = Modifier.padding(start = 12.dp, top = 2.dp))
    }
}

@Composable
fun IconAction(icon: ImageVector, label: String, onClick: () -> Unit) =
    IconButton(onClick = onClick) { Icon(icon, label, tint = White, modifier = Modifier.size(24.dp)) }

/** A rounded card holding rows. */
@Composable
fun CardGroup(content: @Composable () -> Unit) = Column(
    Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(Surface2),
) { content() }

/** A settings-style row: title, optional subtitle, and something on the right. */
@Composable
fun Row2(title: String, subtitle: String? = null, titleColor: Color = White, onClick: (() -> Unit)? = null, trailing: @Composable () -> Unit = {}) {
    Row(
        Modifier.fillMaxWidth().let { if (onClick != null) it.clickable(onClick = onClick) else it }.padding(horizontal = 18.dp, vertical = 15.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = titleColor, fontSize = 16.sp)
            if (subtitle != null) Text(subtitle, color = Grey, fontSize = 13.sp, lineHeight = 17.sp, modifier = Modifier.padding(top = 2.dp))
        }
        trailing()
    }
}

@Composable
fun RowDivider() = Box(Modifier.fillMaxWidth().padding(start = 18.dp).height(0.5.dp).background(Line2))

@Composable
fun FxSwitch(on: Boolean, onChange: (Boolean) -> Unit) = Switch(
    on, onChange,
    colors = SwitchDefaults.colors(checkedTrackColor = Red, checkedThumbColor = OnRed, uncheckedTrackColor = Line2, uncheckedThumbColor = Grey2, uncheckedBorderColor = Line2),
)

@Composable
fun Primary(label: String, enabled: Boolean = true, modifier: Modifier = Modifier.fillMaxWidth(), onClick: () -> Unit) = Button(
    onClick = onClick, enabled = enabled, modifier = modifier.height(54.dp), shape = RoundedCornerShape(27.dp),
    colors = ButtonDefaults.buttonColors(containerColor = Red, contentColor = OnRed, disabledContainerColor = Line2, disabledContentColor = Grey),
) { Text(label, fontWeight = FontWeight.SemiBold, fontSize = 16.sp) }

@Composable
fun Ghost(label: String, modifier: Modifier = Modifier, onClick: () -> Unit) = OutlinedButton(
    onClick = onClick, modifier = modifier.height(50.dp), shape = RoundedCornerShape(25.dp),
    border = BorderStroke(1.dp, Line2),
    colors = ButtonDefaults.outlinedButtonColors(contentColor = White, containerColor = Surface2),
) { Text(label, fontWeight = FontWeight.SemiBold) }

@Composable
fun LinkButton(label: String, color: Color = Red, onClick: () -> Unit) =
    TextButton(onClick = onClick) { Text(label, color = color, fontWeight = FontWeight.SemiBold) }

/** Small rounded choices (auto-lock delay, erase limit…). */
@Composable
fun Chips(options: List<Pair<String, Boolean>>, onPick: (Int) -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEachIndexed { i, (label, on) ->
            val bg by animateColorAsState(if (on) White else Surface3, label = "chip")
            Box(
                Modifier.clip(RoundedCornerShape(18.dp)).background(bg).clickable { onPick(i) }.padding(horizontal = 14.dp, vertical = 9.dp),
            ) { Text(label, color = if (on) Black else White, fontSize = 14.sp, fontWeight = FontWeight.Medium) }
        }
    }
}

// ---------------------------------------------------------------- bottom tabs

@Composable
fun BottomTabs(tabs: List<Pair<ImageVector, String>>, selected: Int, onSelect: (Int) -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(Black).padding(top = 6.dp, bottom = 8.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        tabs.forEachIndexed { i, (icon, label) ->
            val on = i == selected
            Column(
                Modifier.clip(RoundedCornerShape(16.dp)).clickable { onSelect(i) }.padding(horizontal = if (tabs.size > 4) 4.dp else 18.dp, vertical = 4.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Box(
                    Modifier.clip(RoundedCornerShape(14.dp)).background(if (on) Surface3 else Black).padding(horizontal = if (tabs.size > 4) 14.dp else 18.dp, vertical = 5.dp),
                ) { Icon(icon, label, tint = if (on) White else Grey, modifier = Modifier.size(22.dp)) }
                Text(label, color = if (on) White else Grey, fontSize = 12.sp, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
            }
        }
    }
}

/** The bar of actions shown while items are selected (or in the viewer). */
@Composable
fun ActionBar(actions: List<Triple<ImageVector, String, () -> Unit>>, background: Color = Black) {
    val tight = actions.size > 5 // six actions only fit a phone's width as equal columns
    Row(Modifier.fillMaxWidth().background(background).padding(vertical = 6.dp, horizontal = if (tight) 4.dp else 0.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
        actions.forEach { (icon, label, onClick) ->
            Column(
                (if (tight) Modifier.weight(1f) else Modifier).clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick)
                    .padding(horizontal = if (tight) 2.dp else 14.dp, vertical = 6.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(icon, label, tint = if (label == "Delete" || icon == FxIcons.Heart) Red else White, modifier = Modifier.size(22.dp))
                Spacer(Modifier.height(3.dp))
                Text(label, color = if (label == "Delete") Red else White, fontSize = if (tight) 11.sp else 12.sp, maxLines = 1, softWrap = false)
            }
        }
    }
}

// ---------------------------------------------------------------- fields and forms

@Composable
fun TextBox(value: String, onChange: (String) -> Unit, label: String, modifier: Modifier = Modifier.fillMaxWidth()) = OutlinedTextField(
    value = value, onValueChange = onChange, label = { Text(label) }, singleLine = true, shape = RoundedCornerShape(16.dp),
    colors = fieldColors(), modifier = modifier,
)

@Composable
fun fieldColors() = OutlinedTextFieldDefaults.colors(
    focusedTextColor = White, unfocusedTextColor = White, focusedBorderColor = White, unfocusedBorderColor = Line2,
    focusedLabelColor = Grey2, unfocusedLabelColor = Grey, cursorColor = Red,
    focusedContainerColor = Surface2, unfocusedContainerColor = Surface2,
)

@Composable
fun Confirm(title: String, text: String, yes: String, danger: Boolean = true, onYes: () -> Unit, onDismiss: () -> Unit) = AlertDialog(
    onDismissRequest = onDismiss, containerColor = Line, shape = RoundedCornerShape(28.dp),
    title = { Text(title, color = White, fontWeight = FontWeight.Bold) },
    text = { Body(text) },
    confirmButton = { LinkButton(yes, if (danger) Red else White) { onDismiss(); onYes() } },
    dismissButton = { LinkButton("Cancel", Grey2, onDismiss) },
)

// ---------------------------------------------------------------- formatting

fun formatWait(ms: Long): String {
    val s = (ms + 999) / 1000
    return if (s >= 60) "${s / 60} min ${s % 60} s" else "$s s"
}

fun formatDuration(ms: Long): String {
    val s = ms / 1000
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%d:%02d".format(s / 60, s % 60)
}

fun formatDate(ms: Long): String = if (ms <= 0) "" else DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(ms))

fun formatSize(bytes: Long): String = when {
    bytes >= 1L shl 30 -> "%.1f GB".format(bytes / (1L shl 30).toDouble())
    bytes >= 1L shl 20 -> "%.1f MB".format(bytes / (1L shl 20).toDouble())
    else -> "${(bytes + 1023) / 1024} KB"
}

fun Modifier.screenPadding() = this.padding(horizontal = 20.dp, vertical = 16.dp)

@Composable
fun Gap(h: Int) = Spacer(Modifier.height(h.dp))

@Composable
fun HGap(w: Int) = Spacer(Modifier.width(w.dp))
