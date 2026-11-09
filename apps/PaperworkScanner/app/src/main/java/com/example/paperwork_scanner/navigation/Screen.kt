package com.example.paperwork_scanner.navigation

sealed class Screen(val route: String) {
    data object Splash : Screen("splash")
    data object Main : Screen("main")
    data object Scanner : Screen("scanner")
    data object EmailReview : Screen("email_review")
}
