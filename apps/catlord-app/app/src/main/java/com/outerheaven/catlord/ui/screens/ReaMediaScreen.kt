package com.outerheaven.catlord.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.catlord.ui.MainViewModel
import com.outerheaven.catlord.ui.theme.*

@Composable
fun ReaMediaScreen(viewModel: MainViewModel) {
    val mediaStats by viewModel.mediaStats.collectAsState()
    val findings by viewModel.latestFindings.collectAsState()
    val isConnected by viewModel.isConnected.collectAsState()
    val isLoading by viewModel.isLoadingReaMedia.collectAsState()
    val serverUrl by viewModel.serverUrl.collectAsState()

    var showUrlDialog by remember { mutableStateOf(false) }
    var tempUrl by remember { mutableStateOf(serverUrl) }

    LaunchedEffect(Unit) {
        viewModel.loadMediaAndFindings()
        viewModel.checkConnection()
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Cluster Connection Header
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(14.dp)
                                .background(if (isConnected) NeonGreen else DangerRed, CircleShape)
                        )
                        Column {
                            Text(
                                text = if (isConnected) "Metal-Bridge Verbunden" else "Metal-Bridge Getrennt",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Text(
                                text = serverUrl,
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextSecondary,
                                fontSize = 12.sp
                            )
                        }
                    }

                    Row {
                        IconButton(onClick = {
                            tempUrl = serverUrl
                            showUrlDialog = true
                        }) {
                            Icon(Icons.Default.Settings, contentDescription = "Einstellungen", tint = TextSecondary)
                        }

                        IconButton(onClick = { viewModel.loadMediaAndFindings() }) {
                            Icon(Icons.Default.Refresh, contentDescription = "Aktualisieren", tint = NeonCyan)
                        }
                    }
                }
            }
        }

        // Section: Elliot & Käch Datasets / Media Stats
        item {
            Text(
                text = "🐾 Datasets & Media (Elliot & Käch)",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                color = TextPrimary
            )
        }

        if (isLoading && mediaStats == null) {
            item {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    contentAlignment = Alignment.Center
                ) {
                    CircularProgressIndicator(color = NeonCyan)
                }
            }
        } else {
            val stats = mediaStats
            val totals = stats?.totals

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    // Elliot Card
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkSurface),
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.weight(1f)
                    ) {
                        Column(
                            modifier = Modifier.padding(14.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                Icon(Icons.Default.Pets, contentDescription = null, tint = AmberGold, modifier = Modifier.size(20.dp))
                                Text("Elliot (Tank)", fontWeight = FontWeight.Bold, color = AmberGold, fontSize = 14.sp)
                            }
                            Text(
                                text = "${totals?.elliotImages ?: 0} Bilder",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Text(
                                text = "${totals?.elliotVideos ?: 0} Videos",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextSecondary,
                                fontSize = 12.sp
                            )
                            Text(
                                text = stats?.details?.get("elliot")?.sizeFormatted ?: "",
                                style = MaterialTheme.typography.labelSmall,
                                color = TextDisabled
                            )
                        }
                    }

                    // Käch Card
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkSurface),
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.weight(1f)
                    ) {
                        Column(
                            modifier = Modifier.padding(14.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp)
                        ) {
                            Row(
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                Icon(Icons.Default.SportsMartialArts, contentDescription = null, tint = NeonCyan, modifier = Modifier.size(20.dp))
                                Text("Käch / Cash", fontWeight = FontWeight.Bold, color = NeonCyan, fontSize = 14.sp)
                            }
                            Text(
                                text = "${totals?.kaechImages ?: 0} Bilder",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.Bold,
                                color = TextPrimary
                            )
                            Text(
                                text = "${totals?.kaechVideos ?: 0} Videos",
                                style = MaterialTheme.typography.bodyMedium,
                                color = TextSecondary,
                                fontSize = 12.sp
                            )
                            Text(
                                text = stats?.details?.get("kaech")?.sizeFormatted ?: "",
                                style = MaterialTheme.typography.labelSmall,
                                color = TextDisabled
                            )
                        }
                    }
                }
            }

            // Raw Footage & Gesamt
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkSurfaceVariant),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(14.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text("Gesamtes Catlord-Archiv", fontWeight = FontWeight.Bold, color = TextPrimary)
                            Text(
                                text = "NFS: /mnt/storage-beta/projects/catlord-media/",
                                color = TextSecondary,
                                fontSize = 11.sp
                            )
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text(
                                text = "${totals?.totalImages ?: 0} Fotos • ${totals?.totalVideos ?: 0} Videos",
                                fontWeight = FontWeight.Bold,
                                color = NeonGreen
                            )
                            Text(
                                text = stats?.details?.get("raw_footage")?.sizeFormatted ?: "",
                                color = TextDisabled,
                                fontSize = 11.sp
                            )
                        }
                    }
                }
            }
        }

        // Section: REA (Reverse Engineering Analysis) Findings
        item {
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "🔬 REA Sandbox Findings",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                    color = TextPrimary
                )

                Surface(
                    color = CyberViolet.copy(alpha = 0.2f),
                    shape = RoundedCornerShape(6.dp)
                ) {
                    Text(
                        text = "DEEPSEEK / JADX",
                        color = CyberViolet,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                    )
                }
            }
        }

        item {
            findings?.let { f ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkSurface),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = f.filename ?: "llm_findings.md",
                                fontWeight = FontWeight.Bold,
                                color = AmberGold
                            )
                            Text(
                                text = f.pkg ?: "catlord",
                                color = TextSecondary,
                                fontSize = 11.sp
                            )
                        }

                        HorizontalDivider(color = DarkSurfaceElevated)

                        Text(
                            text = f.content ?: "Kein Inhalt vorhanden.",
                            color = TextPrimary,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 12.sp,
                            lineHeight = 18.sp,
                            maxLines = 15
                        )
                    }
                }
            } ?: Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurface),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Icon(Icons.Default.Analytics, contentDescription = null, tint = TextSecondary, modifier = Modifier.size(36.dp))
                    Text("Keine aktuellen Findings gefunden.", color = TextSecondary)
                    Text("Trigger via Web-IDE oder Metal-Bridge decompile ausführen.", color = TextDisabled, fontSize = 11.sp)
                }
            }
        }
    }

    // Server-URL Konfigurations-Dialog
    if (showUrlDialog) {
        AlertDialog(
            onDismissRequest = { showUrlDialog = false },
            title = { Text("Metal-Bridge Server-URL") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Tailscale IP oder Hostname von metal-dgear:")
                    OutlinedTextField(
                        value = tempUrl,
                        onValueChange = { tempUrl = it },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
            },
            confirmButton = {
                Button(onClick = {
                    viewModel.updateServerUrl(tempUrl)
                    showUrlDialog = false
                }) {
                    Text("Speichern")
                }
            },
            dismissButton = {
                TextButton(onClick = { showUrlDialog = false }) {
                    Text("Abbrechen")
                }
            }
        )
    }
}
